"""Wraps the existing echo360 package for use by the web application.
All public functions are designed to run in worker threads, not async coroutines.
"""
import json
import logging
import os
import re
import time

_LOGGER = logging.getLogger(__name__)

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COOKIES_FILE = os.path.join(_PROJ_ROOT, "_browser_persistent_session", "cookies.json")


# ── Driver helpers ────────────────────────────────────────────────────────────

def _build_driver():
    """Build a headless Chrome webdriver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920x1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (iPad; CPU OS 6_0 like Mac OS X) "
        "AppleWebKit/536.26 (KHTML, like Gecko) Version/6.0 Mobile/10A5376e Safari/8536.25"
    )

    # Docker / container environment requires these flags
    if os.environ.get("CHROME_BIN"):
        opts.binary_location = os.environ["CHROME_BIN"]
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    log_path = os.path.join(_PROJ_ROOT, "webdriver_service.log")
    service_kwargs = {"log_output": log_path}
    if os.environ.get("CHROMEDRIVER_PATH"):
        service_kwargs["executable_path"] = os.environ["CHROMEDRIVER_PATH"]

    return webdriver.Chrome(service=Service(**service_kwargs), options=opts)


def _load_session(driver, hostname: str) -> bool:
    """Restore saved cookies and warm up the Echo360 session."""
    if not os.path.exists(_COOKIES_FILE):
        return False
    driver.get(hostname)
    with open(_COOKIES_FILE) as f:
        cookies = json.load(f)
    for cookie in cookies:
        cookie.pop("sameSite", None)
        try:
            driver.add_cookie(cookie)
        except Exception:
            pass
    driver.refresh()
    time.sleep(2)
    return any("ECHO_JWT" in c["name"] for c in driver.get_cookies())


def _extract_hostname(url: str) -> str:
    m = re.search(r"https?://[^/]+", url)
    return m.group() if m else url


def _extract_section_id(url: str) -> str:
    m = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", url, re.I
    )
    return m.group() if m else ""


# ── Courses-page discovery ────────────────────────────────────────────────────

def discover_course_urls(courses_page_url: str) -> list[str]:
    """Navigate to the Echo360 /courses listing page and return all section URLs."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    hostname = _extract_hostname(courses_page_url)
    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, hostname):
            raise RuntimeError(
                "No saved session found. Run the CLI first to log in:\n"
                "  python echo360.py URL --chrome --persistent-session"
            )

        driver.get(courses_page_url)

        # Wait for initial JS render
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )
        time.sleep(2)

        # Echo360 may land on Library — click the Courses nav link if visible
        try:
            courses_link = driver.find_element(By.XPATH, "//a[normalize-space()='Courses'] | //span[normalize-space()='Courses']/parent::a")
            if courses_link:
                courses_link.click()
                time.sleep(3)
        except Exception:
            pass

        uuid_pattern = re.compile(
            r"/section/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            re.I,
        )

        # Poll for up to 20s — the SPA loads course data via XHR after initial render
        page_source = ""
        for _ in range(10):
            time.sleep(2)
            page_source = driver.page_source
            if uuid_pattern.search(page_source):
                break
            # Scroll down to trigger any lazy-loaded content
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")

        current_url = driver.current_url
        _LOGGER.info("discover_course_urls: landed on %s, page length %d", current_url, len(page_source))

        # Save a screenshot for debugging
        try:
            screenshot_path = os.path.join(_PROJ_ROOT, "debug_discover.png")
            driver.save_screenshot(screenshot_path)
            _LOGGER.info("discover_course_urls: screenshot saved to %s", screenshot_path)
        except Exception:
            pass

        if "/login" in current_url or "sign-in" in current_url.lower():
            raise RuntimeError("Session expired — please re-authenticate via the CLI.")

        seen: set[str] = set()
        urls: list[str] = []
        for m in uuid_pattern.finditer(page_source):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                urls.append(f"{hostname}/section/{m.group(1)}/home")

        _LOGGER.info("discover_course_urls: extracted %d unique section URLs", len(urls))

        if not urls:
            raise RuntimeError(
                f"No courses found on that page (landed on: {current_url}). "
                "The session may have expired or the page structure has changed."
            )

        return urls
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── Course sync ───────────────────────────────────────────────────────────────

def sync_course(course_id: int, course_url: str) -> None:
    """Fetch course metadata and populate the lectures table. Runs in a worker thread."""
    from app.db import get_db
    from app import jobs
    from echo360.course import EchoCloudCourse

    def _bcast(data: dict):
        jobs.broadcast({"course_id": course_id, **data})

    _bcast({"type": "sync_start"})
    hostname = _extract_hostname(course_url)
    section_id = _extract_section_id(course_url)

    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, hostname):
            raise RuntimeError(
                "No saved session found. Run the CLI first to log in:\n"
                "  python echo360.py URL --chrome --persistent-session"
            )

        course = EchoCloudCourse(section_id, hostname, alternative_feeds=False)
        course.set_driver(driver)

        course_data = course._get_course_data()
        course_name = course.course_name

        with get_db() as conn:
            conn.execute(
                "UPDATE courses SET name = ?, last_synced_at = datetime('now') WHERE id = ?",
                [course_name, course_id],
            )

        lectures = _parse_lectures(course_data)

        with get_db() as conn:
            for lec in lectures:
                conn.execute(
                    """
                    INSERT INTO lectures (course_id, echo_id, title, date, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(course_id, echo_id) DO UPDATE SET
                        title    = excluded.title,
                        date     = excluded.date,
                        raw_json = excluded.raw_json
                    """,
                    [course_id, lec["echo_id"], lec["title"], lec["date"], lec["raw_json"]],
                )

        _bcast({"type": "sync_done", "course_name": course_name, "count": len(lectures)})

    except Exception as e:
        _LOGGER.exception("sync_course failed for course %d", course_id)
        _bcast({"type": "sync_error", "error": str(e)})
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _parse_lectures(course_data: dict) -> list[dict]:
    results = []
    for v in course_data.get("data", []):
        try:
            if "lessons" in v:
                group_name = v.get("groupInfo", {}).get("name", "")
                for sub in v["lessons"]:
                    lec = _parse_single(sub, group_prefix=group_name)
                    if lec:
                        results.append(lec)
            else:
                lec = _parse_single(v)
                if lec:
                    results.append(lec)
        except (KeyError, TypeError):
            continue
    return results


def _parse_single(v: dict, group_prefix: str = "") -> dict | None:
    try:
        lesson = v["lesson"]["lesson"]
        echo_id = str(lesson["id"])
        title = lesson.get("name", "Untitled")
        if group_prefix:
            title = f"{group_prefix} - {title}"

        date = "1970-01-01"
        if v["lesson"].get("startTimeUTC"):
            date = v["lesson"]["startTimeUTC"][:10]
        elif lesson.get("createdAt"):
            date = lesson["createdAt"][:10]

        return {"echo_id": echo_id, "title": title, "date": date, "raw_json": json.dumps(v)}
    except (KeyError, TypeError):
        return None


# ── Lecture download ──────────────────────────────────────────────────────────

def _extract_stream_url(video_json: dict, hostname: str):
    """Extract stream URL from raw lesson JSON without launching Chrome.

    Returns a URL string (direct MP4) or list of M3U8 URL strings, or None.
    Mirrors the from_json_mp4 / from_json_m3u8 logic in EchoCloudVideo.
    """
    # Method 1: direct S3 MP4 URL from primaryFiles
    try:
        primary = video_json["lesson"]["video"]["media"]["media"]["current"]["primaryFiles"]
        urls = [obj["s3Url"] for obj in primary if obj.get("s3Url")]
        if urls:
            return next(reversed(urls))
    except (KeyError, TypeError):
        pass

    # Method 2: M3U8 manifests
    try:
        if not (video_json["lesson"].get("hasVideo") and video_json["lesson"].get("hasAvailableVideo")):
            return None
        manifests = video_json["lesson"]["video"]["media"]["media"]["versions"][0]["manifests"]
        m3u8urls = [m["uri"] for m in manifests if m.get("uri")]
        if not m3u8urls:
            return None
        from urllib.parse import urlparse
        netloc = urlparse(hostname).netloc
        fixed = []
        for u in m3u8urls:
            p = urlparse(u)
            fixed.append(f"{p.scheme}://content.{netloc}{p.path}")
        return fixed
    except (KeyError, TypeError, IndexError):
        pass

    return None


def _build_session_from_cookies() -> "requests.Session":
    """Build a requests.Session loaded with saved Echo360 cookies."""
    import requests as req
    session = req.Session()
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            cookies = json.load(f)
        for c in cookies:
            session.cookies.set(c["name"], c["value"])
    return session


def _download_audio_direct(session, stream_url, output_dir: str, filename: str) -> str | None:
    """Download audio to opus without Chrome. Returns opus path or None on failure."""
    from echo360.hls_downloader import Downloader, urljoin
    from echo360.naive_m3u8_parser import NaiveM3U8Parser
    from echo360.videos import EchoCloudVideo

    urls = stream_url if isinstance(stream_url, list) else [stream_url]
    single_url = urls[0]

    # Convert session cookies to the format Downloader expects
    selenium_cookies = [{"name": k, "value": v} for k, v in session.cookies.items()]

    if single_url.endswith(".m3u8"):
        r = session.get(single_url, timeout=20)
        if not r.ok:
            _LOGGER.error("Failed to fetch m3u8: %s", r.status_code)
            return None

        lines = r.content.decode().split("\n")
        parser = NaiveM3U8Parser(lines)
        try:
            parser.parse()
        except Exception as e:
            _LOGGER.error("Failed to parse m3u8: %s", e)
            return None

        m3u8_video, m3u8_audio = parser.get_video_and_audio()
        audio_m3u8 = urljoin(single_url, m3u8_audio) if m3u8_audio else (
            urljoin(single_url, m3u8_video) if m3u8_video else None
        )
        if not audio_m3u8:
            _LOGGER.error("No audio or video stream found in m3u8")
            return None

        downloader = Downloader(50, selenium_cookies=selenium_cookies)
        downloader.run(audio_m3u8, output_dir, convert_to_mp4=False)
        raw_file = downloader.result_file_name
    else:
        # Direct file URL (MP4, ts, etc.) — stream-download it
        ext = single_url.split("?")[0].split(".")[-1]
        raw_file = os.path.join(output_dir, f"{filename}_raw.{ext}")
        r = session.get(single_url, stream=True, timeout=30)
        if not r.ok:
            _LOGGER.error("Failed to download direct URL: %s", r.status_code)
            return None
        with open(raw_file, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)

    opus_file = os.path.join(output_dir, filename + ".opus")
    if EchoCloudVideo._convert_to_opus(raw_file, opus_file):
        try:
            os.remove(raw_file)
        except OSError:
            pass
        return opus_file

    return None


def download_lecture(lecture_id: int, output_dir: str) -> None:
    """Download audio for a single lecture. Runs in a worker thread."""
    from app.db import get_db
    from app import jobs

    row = None

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": row["course_id"] if row else None, **data})

    with get_db() as conn:
        row = conn.execute(
            """
            SELECT l.*, c.hostname, c.name AS course_name
            FROM   lectures l
            JOIN   courses  c ON l.course_id = c.id
            WHERE  l.id = ?
            """,
            [lecture_id],
        ).fetchone()

    if row is None:
        raise ValueError(f"Lecture {lecture_id} not found")

    # Skip if already done
    if row["audio_status"] == "done" and row["audio_path"] and os.path.exists(row["audio_path"]):
        _bcast({"status": "done", "audio_path": row["audio_path"]})
        return

    _set_status(lecture_id, "downloading")
    _bcast({"status": "downloading"})

    os.makedirs(output_dir, exist_ok=True)

    safe_name = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")
    filename = safe_name[:150]

    # ── Fast path: no Chrome ──────────────────────────────────────────────────
    video_json = json.loads(row["raw_json"])
    stream_url = _extract_stream_url(video_json, row["hostname"])

    if stream_url:
        try:
            session = _build_session_from_cookies()
            audio_path = _download_audio_direct(session, stream_url, output_dir, filename)
            if audio_path:
                _set_status(lecture_id, "done", audio_path=audio_path)
                _bcast({"status": "done", "audio_path": audio_path})
                return
            _LOGGER.warning("Fast download path returned no file for lecture %d, falling back to Chrome", lecture_id)
        except Exception:
            _LOGGER.warning("Fast download path failed for lecture %d, falling back to Chrome", lecture_id, exc_info=True)

    # ── Slow fallback: Chrome ─────────────────────────────────────────────────
    _LOGGER.info("Using Chrome fallback for lecture %d", lecture_id)
    from echo360.videos import EchoCloudVideo

    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, row["hostname"]):
            raise RuntimeError("No valid session. Please re-authenticate via the CLI.")

        video = EchoCloudVideo(video_json, driver, row["hostname"], alternative_feeds=False)
        result = video.download(output_dir, filename, audio_only=True)

        if result:
            opus_path = os.path.join(output_dir, filename + ".opus")
            audio_path = opus_path if os.path.exists(opus_path) else None
            _set_status(lecture_id, "done", audio_path=audio_path)
            _bcast({"status": "done", "audio_path": audio_path})
        else:
            _set_status(lecture_id, "error")
            _bcast({"status": "error"})

    except Exception as e:
        _LOGGER.exception("download_lecture failed for lecture %d", lecture_id)
        _set_status(lecture_id, "error")
        _bcast({"status": "error", "error": str(e)})
        raise
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def _set_status(lecture_id: int, status: str, audio_path: str | None = None) -> None:
    from app.db import get_db

    with get_db() as conn:
        if audio_path is not None:
            conn.execute(
                "UPDATE lectures SET audio_status = ?, audio_path = ? WHERE id = ?",
                [status, audio_path, lecture_id],
            )
        else:
            conn.execute(
                "UPDATE lectures SET audio_status = ? WHERE id = ?",
                [status, lecture_id],
            )
