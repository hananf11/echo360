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

        # Wait for the page to finish its initial JS render
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "body"))
        )
        time.sleep(3)

        current_url = driver.current_url
        page_source = driver.page_source
        _LOGGER.info("discover_course_urls: landed on %s", current_url)

        # If redirected to a login page, raise a clear error
        if "/login" in current_url or "sign-in" in current_url.lower():
            raise RuntimeError("Session expired — please re-authenticate via the CLI.")

        # Collect all hrefs from the page that contain a section UUID
        all_hrefs: list[str] = driver.execute_script(
            "return Array.from(document.querySelectorAll('[href]'))"
            ".map(el => el.getAttribute('href') || '')"
        )
        _LOGGER.info("discover_course_urls: found %d hrefs on page", len(all_hrefs))

        # Also scan the raw page source for any section UUIDs we might have missed
        uuid_pattern = re.compile(
            r"/section/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            re.I,
        )
        seen: set[str] = set()
        urls: list[str] = []

        for href in all_hrefs:
            m = uuid_pattern.search(href)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                urls.append(f"{hostname}/section/{m.group(1)}/home")

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

def download_lecture(lecture_id: int, output_dir: str) -> None:
    """Download audio for a single lecture. Runs in a worker thread."""
    from app.db import get_db
    from app import jobs
    from echo360.videos import EchoCloudVideo

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, **data})

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

    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, row["hostname"]):
            raise RuntimeError("No valid session. Please re-authenticate via the CLI.")

        video_json = json.loads(row["raw_json"])
        video = EchoCloudVideo(video_json, driver, row["hostname"], alternative_feeds=False)

        safe_name = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")
        filename = safe_name[:150]

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
