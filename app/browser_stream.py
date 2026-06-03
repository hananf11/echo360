"""CDP-based browser screencast for in-UI Echo360 re-authentication.

Launches a Chrome instance, streams its viewport via CDP screencast over a
FastAPI WebSocket, and forwards mouse/keyboard input from the frontend.
Automatically detects ECHO_JWT cookie and saves the session.
"""

import asyncio
import base64
import json
import logging
import os
import signal
import subprocess
import time

import httpx
import websockets

_LOGGER = logging.getLogger(__name__)

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COOKIES_FILE = os.path.join(_PROJ_ROOT, "_browser_persistent_session", "cookies.json")

_CDP_PORT = 9222
_VIEWPORT_WIDTH = 1280
_VIEWPORT_HEIGHT = 800

# Global ref so only one login session runs at a time
_active_process: subprocess.Popen | None = None


def _chrome_binary() -> str:
    return os.environ.get("CHROME_BIN", "google-chrome")


def _jwt_exp(token: str) -> int | None:
    """Return the `exp` (unix seconds) claim from a JWT, or None if unreadable.

    Payload only — no signature verification (we just need the expiry time).
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except (IndexError, ValueError, json.JSONDecodeError, TypeError):
        return None


def check_session_status() -> dict:
    """Check whether a non-expired Echo360 session cookie exists.

    The ECHO_JWT is a short-lived (~13h) JWT and is stored as a session cookie
    (no cookie-level expiry), so the only reliable signal is the JWT `exp` claim.
    Returns `valid` reflecting actual expiry, plus `expires_at` / `expires_in`
    so the UI can warn before the token dies instead of failing silently.
    """
    if not os.path.exists(_COOKIES_FILE):
        return {"valid": False, "cookies_exist": False, "expires_at": None, "expires_in": None}
    try:
        with open(_COOKIES_FILE) as f:
            cookies = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"valid": False, "cookies_exist": True, "expires_at": None, "expires_in": None}

    jwt = next((c.get("value", "") for c in cookies if c.get("name") == "ECHO_JWT"), None)
    if not jwt:
        return {"valid": False, "cookies_exist": True, "expires_at": None, "expires_in": None}

    exp = _jwt_exp(jwt)
    if exp is None:
        # Token present but unreadable — treat as present-but-unknown, lean invalid
        _LOGGER.warning("check_session_status: ECHO_JWT present but exp claim unreadable")
        return {"valid": False, "cookies_exist": True, "expires_at": None, "expires_in": None}

    now = int(time.time())
    expires_in = exp - now
    valid = expires_in > 0
    _LOGGER.debug("check_session_status: valid=%s expires_in=%ds", valid, expires_in)
    return {
        "valid": valid,
        "cookies_exist": True,
        "expires_at": exp,
        "expires_in": expires_in,
    }


def _launch_chrome(url: str, port: int = _CDP_PORT) -> subprocess.Popen:
    """Launch Chrome with remote debugging enabled."""
    chrome = _chrome_binary()
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--window-size={_VIEWPORT_WIDTH},{_VIEWPORT_HEIGHT}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-extensions",
        # headless=new is a full renderer (SSO pages work) and supports
        # Page.startScreencast — required since the container has no display.
        "--headless=new",
        url,
    ]
    _LOGGER.info("Launching Chrome: %s", " ".join(args))
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def _wait_for_cdp(port: int = _CDP_PORT, timeout: float = 15.0) -> str:
    """Poll the CDP HTTP endpoint until it's ready; return the WS debugger URL."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"http://127.0.0.1:{port}/json")
                targets = resp.json()
                for t in targets:
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
            except Exception:
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError("Chrome CDP did not become ready in time")


def _kill_chrome(proc: subprocess.Popen | None):
    """Terminate Chrome process."""
    global _active_process
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _active_process = None


def _save_cookies(cookies: list[dict]):
    """Save cookies to the persistent session file."""
    os.makedirs(os.path.dirname(_COOKIES_FILE), exist_ok=True)
    with open(_COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    _LOGGER.info("Session cookies saved to %s", _COOKIES_FILE)


def try_silent_refresh(url: str = "https://echo360.net.au") -> bool:
    """Attempt to refresh the session silently using headless Chrome.

    If the SSO session is still alive, navigating to Echo360 will
    automatically issue a new ECHO_JWT without interactive login.
    Returns True if refresh succeeded.
    """
    from app.scraper import _build_driver, _load_session, _extract_hostname

    hostname = _extract_hostname(url)
    driver = None
    try:
        driver = _build_driver()
        # Load existing (possibly stale) cookies
        if not os.path.exists(_COOKIES_FILE):
            return False
        _load_session(driver, hostname)

        # Navigate — if SSO session is still valid, Echo360 will redirect back
        # with fresh cookies rather than showing the login page
        driver.get(url)
        import time as _time
        _time.sleep(5)

        current_url = driver.current_url
        if "/login" in current_url or "sign-in" in current_url.lower():
            _LOGGER.info("Silent refresh failed — landed on login page: %s", current_url)
            return False

        # Check for ECHO_JWT
        cookies = driver.get_cookies()
        if any("ECHO_JWT" in c.get("name", "") for c in cookies):
            _save_cookies(cookies)
            _LOGGER.info("Silent refresh succeeded — new ECHO_JWT saved")
            return True

        _LOGGER.info("Silent refresh failed — no ECHO_JWT in cookies")
        return False
    except Exception:
        _LOGGER.exception("Silent refresh error")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def run_browser_stream(ws_frontend, login_url: str):
    """Main entry point: launch Chrome, bridge CDP <-> frontend WebSocket.

    ws_frontend: a FastAPI WebSocket (already accepted).
    login_url: the Echo360 URL to navigate to for login.
    """
    global _active_process

    if _active_process and _active_process.poll() is None:
        _kill_chrome(_active_process)

    proc = _launch_chrome(login_url)
    _active_process = proc

    try:
        await ws_frontend.send_json({"type": "status", "message": "Launching browser..."})
        cdp_ws_url = await _wait_for_cdp()
        await ws_frontend.send_json({"type": "status", "message": "Connected to browser"})

        async with websockets.connect(cdp_ws_url, max_size=16 * 1024 * 1024) as cdp:
            msg_id = 0

            async def cdp_send(method: str, params: dict | None = None) -> int:
                nonlocal msg_id
                msg_id += 1
                await cdp.send(json.dumps({
                    "id": msg_id,
                    "method": method,
                    "params": params or {},
                }))
                return msg_id

            # Enable domains
            await cdp_send("Page.enable")
            await cdp_send("Network.enable")

            # Start screencast
            await cdp_send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 60,
                "maxWidth": _VIEWPORT_WIDTH,
                "maxHeight": _VIEWPORT_HEIGHT,
                "everyNthFrame": 1,
            })

            login_detected = asyncio.Event()

            async def check_cookies():
                """Periodically check for ECHO_JWT cookie."""
                while not login_detected.is_set():
                    await asyncio.sleep(2)
                    try:
                        cookie_id = await cdp_send("Network.getCookies")
                        # We need to read the response from the CDP stream,
                        # but that's handled in forward_frames. Instead, send
                        # and let forward_frames pick up the result.
                    except Exception:
                        pass

            async def forward_frames():
                """Read CDP messages, forward screencast frames, check cookie responses."""
                async for raw in cdp:
                    msg = json.loads(raw)

                    # Screencast frame
                    if msg.get("method") == "Page.screencastFrame":
                        params = msg["params"]
                        await cdp_send("Page.screencastFrameAck", {
                            "sessionId": params["sessionId"],
                        })
                        try:
                            await ws_frontend.send_json({
                                "type": "frame",
                                "data": params["data"],
                                "metadata": params.get("metadata", {}),
                            })
                        except Exception:
                            return

                    # Cookie response (from Network.getCookies)
                    if "result" in msg and "cookies" in msg.get("result", {}):
                        cookies = msg["result"]["cookies"]
                        if any("ECHO_JWT" in c.get("name", "") for c in cookies):
                            # Convert CDP cookies to Selenium-compatible format
                            selenium_cookies = []
                            for c in cookies:
                                sc = {
                                    "name": c["name"],
                                    "value": c["value"],
                                    "domain": c.get("domain", ""),
                                    "path": c.get("path", "/"),
                                    "secure": c.get("secure", False),
                                    "httpOnly": c.get("httpOnly", False),
                                }
                                if c.get("expires", -1) > 0:
                                    sc["expiry"] = int(c["expires"])
                                selenium_cookies.append(sc)
                            _save_cookies(selenium_cookies)
                            login_detected.set()
                            try:
                                await ws_frontend.send_json({"type": "login_success"})
                            except Exception:
                                pass
                            return

            async def handle_input():
                """Read input events from frontend WebSocket and dispatch to Chrome."""
                try:
                    while True:
                        raw = await ws_frontend.receive_text()
                        event = json.loads(raw)

                        if event.get("type") == "mouse":
                            await cdp_send("Input.dispatchMouseEvent", event["params"])
                        elif event.get("type") == "key":
                            await cdp_send("Input.dispatchKeyEvent", event["params"])
                        elif event.get("type") == "scroll":
                            await cdp_send("Input.dispatchMouseEvent", {
                                "type": "mouseWheel",
                                "x": event["x"],
                                "y": event["y"],
                                "deltaX": event.get("deltaX", 0),
                                "deltaY": event.get("deltaY", 0),
                            })
                        elif event.get("type") == "close":
                            return
                except Exception:
                    return

            # Run all three tasks concurrently; any finishing ends the session
            done, pending = await asyncio.wait(
                [
                    asyncio.ensure_future(forward_frames()),
                    asyncio.ensure_future(handle_input()),
                    asyncio.ensure_future(check_cookies()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    except Exception as e:
        _LOGGER.exception("Browser stream error")
        try:
            await ws_frontend.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _kill_chrome(proc)
