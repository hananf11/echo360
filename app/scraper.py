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

    _LOGGER.debug("Building Chrome driver (headless=new, log=%s)", log_path)
    driver = webdriver.Chrome(service=Service(**service_kwargs), options=opts)
    _LOGGER.debug("Chrome driver ready")
    return driver


def _load_session(driver, hostname: str) -> bool:
    """Restore saved cookies and warm up the Echo360 session."""
    if not os.path.exists(_COOKIES_FILE):
        _LOGGER.warning("_load_session: no cookies file found at %s", _COOKIES_FILE)
        return False
    driver.get(hostname)
    with open(_COOKIES_FILE) as f:
        cookies = json.load(f)
    _LOGGER.debug("_load_session: loading %d cookies for %s", len(cookies), hostname)
    loaded, skipped = 0, 0
    for cookie in cookies:
        cookie.pop("sameSite", None)
        try:
            driver.add_cookie(cookie)
            loaded += 1
        except Exception as exc:
            _LOGGER.debug("_load_session: skipped cookie %r — %s", cookie.get("name"), exc)
            skipped += 1
    driver.refresh()
    time.sleep(2)
    has_jwt = any("ECHO_JWT" in c["name"] for c in driver.get_cookies())
    _LOGGER.info(
        "_load_session: %d cookies loaded, %d skipped — ECHO_JWT present: %s",
        loaded, skipped, has_jwt,
    )
    return has_jwt


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
            from app import jobs
            jobs.broadcast({"type": "session_expired"})
            raise RuntimeError(
                "No saved session found. Use the Re-authenticate button in the web UI, "
                "or run: python echo360.py URL --chrome --persistent-session"
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
            from app import jobs
            jobs.broadcast({"type": "session_expired"})
            raise RuntimeError("Session expired — use the Re-authenticate button in the web UI.")

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
    from app.database import get_db
    from app.models import Course, Lecture
    from app import jobs
    from echo360.course import EchoCloudCourse
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from datetime import datetime, timezone

    def _bcast(data: dict):
        jobs.broadcast({"course_id": course_id, **data})

    _bcast({"type": "sync_start"})
    hostname = _extract_hostname(course_url)
    section_id = _extract_section_id(course_url)
    _LOGGER.info("sync_course[%d]: hostname=%s section_id=%s", course_id, hostname, section_id)

    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, hostname):
            jobs.broadcast({"type": "session_expired"})
            raise RuntimeError(
                "No saved session found. Use the Re-authenticate button in the web UI, "
                "or run: python echo360.py URL --chrome --persistent-session"
            )

        _LOGGER.info("sync_course[%d]: session OK, fetching course data", course_id)
        course = EchoCloudCourse(section_id, hostname, alternative_feeds=False)
        course.set_driver(driver)

        course_data = course._get_course_data()
        course_name = course.course_name
        _LOGGER.info("sync_course[%d]: course_name=%r, raw data keys=%s",
                     course_id, course_name, list(course_data.keys()) if isinstance(course_data, dict) else type(course_data).__name__)

        with get_db() as session:
            c = session.get(Course, course_id)
            if c:
                c.name = course_name
                c.last_synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        lectures = _parse_lectures(course_data)
        _LOGGER.info("sync_course[%d]: parsed %d lectures", course_id, len(lectures))

        with get_db() as session:
            for lec in lectures:
                stmt = sqlite_insert(Lecture).values(
                    course_id=course_id,
                    echo_id=lec["echo_id"],
                    title=lec["title"],
                    date=lec["date"],
                    raw_json=lec["raw_json"],
                    duration_seconds=lec.get("duration_seconds"),
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["course_id", "echo_id"],
                    set_={
                        "date": stmt.excluded.date,
                        "raw_json": stmt.excluded.raw_json,
                        "duration_seconds": stmt.excluded.duration_seconds,
                    },
                )
                session.execute(stmt)

            # Reset no_media lectures whose raw_json now indicates media may be available
            no_media_lectures = (
                session.query(Lecture)
                .filter(Lecture.course_id == course_id, Lecture.audio_status == "no_media")
                .all()
            )
            for lec in no_media_lectures:
                try:
                    data = json.loads(lec.raw_json)
                    lesson = data.get("lesson", data)
                    has_content = lesson.get("hasContent", False)
                    has_media = bool(lesson.get("medias"))
                    has_video = lesson.get("hasVideo", False)
                    is_past = lesson.get("isPast", False)
                    # Reset if media is now available, or if a previously-future
                    # lecture is now past (let the download pipeline re-evaluate)
                    if has_content or has_media or (is_past and has_video):
                        lec.audio_status = "pending"
                        lec.error_message = None
                except (json.JSONDecodeError, TypeError):
                    pass

        _LOGGER.info("sync_course[%d]: done — %d lectures upserted", course_id, len(lectures))
        _bcast({"type": "sync_done", "course_name": course_name, "count": len(lectures)})

    except Exception as e:
        _LOGGER.exception("sync_course[%d]: failed", course_id)
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
        else:
            # No scheduled time — try to recover date from captureOccurrenceId
            # e.g. "uuid_20250217T0100" → "2025-02-17"
            medias = v["lesson"].get("medias") or []
            cap_id = medias[0].get("captureOccurrenceId", "") if medias else ""
            m = re.search(r"_(\d{4})(\d{2})(\d{2})T", cap_id)
            if m:
                date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif lesson.get("createdAt"):
                date = lesson["createdAt"][:10]

        duration_seconds = _compute_duration(v)

        return {"echo_id": echo_id, "title": title, "date": date, "raw_json": json.dumps(v), "duration_seconds": duration_seconds}
    except (KeyError, TypeError):
        return None


def _compute_duration(v: dict) -> int | None:
    """Compute lecture duration in seconds from start/end timestamps."""
    from datetime import datetime
    try:
        start = v["lesson"].get("startTimeUTC")
        end = v["lesson"].get("endTimeUTC")
        if start and end:
            fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
            dt_start = datetime.strptime(start, fmt)
            dt_end = datetime.strptime(end, fmt)
            secs = int((dt_end - dt_start).total_seconds())
            if secs > 0:
                return secs
    except (ValueError, TypeError):
        pass
    # Fallback: try timing object
    try:
        timing = v["lesson"]["lesson"].get("timing", {})
        if timing.get("start") and timing.get("end"):
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            dt_start = datetime.strptime(timing["start"], fmt)
            dt_end = datetime.strptime(timing["end"], fmt)
            secs = int((dt_end - dt_start).total_seconds())
            if secs > 0:
                return secs
    except (ValueError, TypeError):
        pass
    return None


# ── Stream URL extraction ─────────────────────────────────────────────────────

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
            url = next(reversed(urls))
            _LOGGER.debug("_extract_stream_url: method=s3_primary url=%s", url[:80])
            return url
    except (KeyError, TypeError):
        pass

    # Method 2: M3U8 manifests
    try:
        lesson = video_json["lesson"]
        has_video = lesson.get("hasVideo")
        has_content = lesson.get("hasContent")
        has_media = bool(lesson.get("medias"))
        if not (has_video or has_content or has_media):
            _LOGGER.debug(
                "_extract_stream_url: no media flags (hasVideo=%s hasContent=%s medias=%s)",
                has_video, has_content, has_media,
            )
            return None
        manifests = video_json["lesson"]["video"]["media"]["media"]["versions"][0]["manifests"]
        m3u8urls = [m["uri"] for m in manifests if m.get("uri")]
        if not m3u8urls:
            _LOGGER.debug("_extract_stream_url: manifests key exists but no URIs")
            return None
        from urllib.parse import urlparse
        netloc = urlparse(hostname).netloc
        fixed = []
        for u in m3u8urls:
            p = urlparse(u)
            fixed.append(f"{p.scheme}://content.{netloc}{p.path}")
        _LOGGER.debug("_extract_stream_url: method=m3u8 count=%d", len(fixed))
        return fixed
    except (KeyError, TypeError, IndexError) as exc:
        _LOGGER.debug("_extract_stream_url: method=m3u8 failed (%s)", exc)

    _LOGGER.debug("_extract_stream_url: no URL found")
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
