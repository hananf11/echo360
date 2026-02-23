"""Extract video frames at specific timestamps from Echo360 lectures."""
import asyncio
import json
import logging
import os
import re
import tempfile
from urllib.parse import urlparse

import httpx

from app import async_downloader, jobs
from app.database import get_db
from app.models import Course, Lecture, Note
from app.scraper import _COOKIES_FILE, _extract_stream_url

_LOGGER = logging.getLogger(__name__)


def _select_segments(
    timestamps: list[float], segments: list[tuple[str, float]]
) -> dict[int, list[tuple[float, float]]]:
    """Map target timestamps to segments.

    Returns {segment_index: [(timestamp, offset_within_segment), ...]}.
    Multiple timestamps in the same segment share one download.
    """
    result: dict[int, list[tuple[float, float]]] = {}
    cumulative = 0.0
    seg_starts: list[float] = []
    for _, dur in segments:
        seg_starts.append(cumulative)
        cumulative += dur

    for ts in sorted(timestamps):
        # Find the segment containing this timestamp
        seg_idx = 0
        for i, start in enumerate(seg_starts):
            if start <= ts:
                seg_idx = i
            else:
                break
        offset = ts - seg_starts[seg_idx]
        result.setdefault(seg_idx, []).append((ts, offset))

    return result


def _build_cookies() -> dict[str, str]:
    """Load saved session cookies for httpx."""
    cookies = {}
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            for c in json.load(f):
                cookies[c["name"]] = c["value"]
    return cookies


def _cookie_header(cookies: dict[str, str]) -> str:
    """Build a Cookie header string for ffmpeg -headers."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _safe_course_dir(course_name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", course_name)


def _safe_filename(date: str, title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", f"{date} - {title}")[:150]


def _resolve_stream_url_chrome(video_json: dict, hostname: str) -> str | list[str] | None:
    """Use headless Chrome to load the classroom page and extract video URLs.

    Chrome executes JS that fetches signed/playable URLs — httpx only gets
    template URLs that return 403.  Runs synchronously (call from executor).
    """
    from app.scraper import _build_driver, _load_session

    url = _extract_stream_url(video_json, hostname)
    if url:
        return url

    lesson_id = video_json.get("lesson", {}).get("lesson", {}).get("id")
    if not lesson_id:
        return None

    classroom_url = f"{hostname}/lesson/{lesson_id}/classroom"
    _LOGGER.info("Loading classroom page via Chrome: %s", classroom_url)

    # Get section_id from the lesson JSON for session warmup
    section_id = video_json.get("lesson", {}).get("lesson", {}).get("sectionId")

    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, hostname):
            raise RuntimeError("No valid session for Chrome")

        import time as _time

        # Visit section home first to establish session context
        if section_id:
            _LOGGER.info("Warming up session at section home: %s", section_id)
            driver.get(f"{hostname}/section/{section_id}/home")
            _time.sleep(3)

        # Retry up to 3 times — mirrors the original CLI's brute_force approach
        for attempt in range(3):
            driver.get(classroom_url)
            _time.sleep(10)  # wait for JS to render video player

            page = driver.page_source.replace("\\/", "/")

            # Extract full content URLs including query params (auth tokens)
            all_urls = re.findall(r'https://content[^"\\]*', page)
            m3u8_urls = [u for u in all_urls if ".m3u8" in u]
            _LOGGER.info("Chrome attempt %d: found %d m3u8 URLs", attempt + 1, len(m3u8_urls))

            if m3u8_urls:
                # Deduplicate preserving order
                seen = set()
                unique = []
                for u in m3u8_urls:
                    if u not in seen:
                        seen.add(u)
                        unique.append(u)

                # Prefer video-only for frame extraction (smaller downloads)
                v_urls = [u for u in unique if "_v.m3u8" in u.split("?")[0]]
                if v_urls:
                    return v_urls
                av_urls = [u for u in unique if "_av.m3u8" in u.split("?")[0]]
                if av_urls:
                    return av_urls
                return unique

            # Try MP4
            mp4_urls = [u for u in all_urls if ".mp4" in u]
            if mp4_urls:
                return mp4_urls[-1]

            _LOGGER.warning("Chrome attempt %d: no video URLs found, retrying...", attempt + 1)

        _LOGGER.error("Chrome: no video URLs found after 3 attempts for %s", classroom_url)
        return None

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def _extract_frame_ffmpeg(
    input_path: str, offset: float, output_path: str, cookies: dict[str, str] | None = None, is_url: bool = False
) -> bool:
    """Extract a single frame using ffmpeg. Returns True on success."""
    cmd = ["ffmpeg", "-loglevel", "error"]
    if is_url and cookies:
        cookie_str = _cookie_header(cookies)
        cmd += ["-headers", f"Cookie: {cookie_str}\r\n"]
    cmd += ["-ss", f"{offset:.3f}", "-i", input_path, "-vframes", "1", "-q:v", "2", output_path]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        _LOGGER.warning("ffmpeg frame extraction failed: %s", stderr.decode()[:300])
        return False
    return os.path.exists(output_path)


async def extract_frames(lecture_id: int) -> None:
    """Extract video frames at timestamps identified by note generation."""
    audio_dir = os.environ.get("ECHO360_AUDIO_DIR", os.path.expanduser("~/echo360-library"))

    # Load lecture and course info
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise ValueError(f"Lecture {lecture_id} not found")
        row = lec.to_dict()
        course_name = lec.course.name
        hostname = lec.course.hostname

        # Get latest note with frame_timestamps
        note = (
            session.query(Note)
            .filter(Note.lecture_id == lecture_id)
            .order_by(Note.id.desc())
            .first()
        )
        if not note or not note.frame_timestamps:
            _LOGGER.info("No frame timestamps for lecture %d, skipping", lecture_id)
            return

        frame_timestamps = json.loads(note.frame_timestamps)

    if not frame_timestamps:
        return

    course_id = row["course_id"]

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    # Update status
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.frames_status = "extracting"
    _bcast({"frames_status": "extracting"})
    jobs.broadcast({"type": "frames_start", "lecture_id": lecture_id, "course_id": course_id})

    try:
        # Set up output directory
        course_dir = os.path.join(audio_dir, _safe_course_dir(course_name))
        frames_dir = os.path.join(course_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        filename_base = _safe_filename(row["date"], row["title"])
        target_times = [ft["time"] for ft in frame_timestamps]

        cookies = _build_cookies()

        # Get stream URL — tries raw_json first, then Chrome fallback
        video_json = json.loads(row["raw_json"])
        loop = asyncio.get_running_loop()
        stream_url = await loop.run_in_executor(
            jobs._blocking_executor,
            _resolve_stream_url_chrome, video_json, hostname,
        )

        if not stream_url:
            raise RuntimeError("No stream URL found for lecture")
        extracted_frames = []

        urls = stream_url if isinstance(stream_url, list) else [stream_url]
        single_url = urls[0]
        # Check path portion for extension (URL may have query params)
        url_path = single_url.split("?")[0]

        if not url_path.endswith(".m3u8"):
            # Direct MP4 — use ffmpeg with -ss seeking directly into URL
            for ft in frame_timestamps:
                ts = ft["time"]
                out_path = os.path.join(frames_dir, f"{filename_base}_{int(ts)}s.jpg")
                if await _extract_frame_ffmpeg(single_url, ts, out_path, cookies=cookies, is_url=True):
                    extracted_frames.append({"time": ts, "reason": ft["reason"], "path": out_path})
                else:
                    _LOGGER.warning("Failed to extract frame at %ds for lecture %d", ts, lecture_id)
        else:
            # M3U8 — download only needed segments, extract frames locally
            async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as client:
                segments = await async_downloader.resolve_video_m3u8(client, single_url)
                selected = _select_segments(target_times, segments)

                # Download needed segments to temp files
                with tempfile.TemporaryDirectory(dir=frames_dir) as tmp_dir:
                    seg_paths: dict[int, str] = {}
                    for seg_idx in selected:
                        seg_url = segments[seg_idx][0]
                        seg_path = os.path.join(tmp_dir, f"seg_{seg_idx}.ts")
                        for attempt in range(3):
                            try:
                                r = await client.get(seg_url, timeout=30)
                                r.raise_for_status()
                                with open(seg_path, "wb") as f:
                                    f.write(r.content)
                                seg_paths[seg_idx] = seg_path
                                break
                            except Exception:
                                if attempt == 2:
                                    _LOGGER.warning("Failed to download segment %d after 3 attempts", seg_idx)
                                await asyncio.sleep(1)

                    # Extract frames from downloaded segments
                    for seg_idx, ts_list in selected.items():
                        if seg_idx not in seg_paths:
                            continue
                        seg_path = seg_paths[seg_idx]
                        for ts, offset in ts_list:
                            # Find the matching reason
                            reason = next((ft["reason"] for ft in frame_timestamps if ft["time"] == ts), "")
                            out_path = os.path.join(frames_dir, f"{filename_base}_{int(ts)}s.jpg")
                            if await _extract_frame_ffmpeg(seg_path, offset, out_path):
                                extracted_frames.append({"time": ts, "reason": reason, "path": out_path})
                            else:
                                _LOGGER.warning("Failed to extract frame at %ds for lecture %d", ts, lecture_id)
                    # tmp_dir auto-cleaned (segments deleted)

        _LOGGER.info("Extracted %d frames for lecture %d", len(extracted_frames), lecture_id)

        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.frames_status = "done"
        _bcast({"frames_status": "done"})
        jobs.broadcast({"type": "frames_done", "lecture_id": lecture_id, "course_id": course_id})

    except Exception as e:
        _LOGGER.exception("Frame extraction failed for lecture %d", lecture_id)
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.frames_status = "error"
                lec.error_message = f"Frame extraction: {e}"
        _bcast({"frames_status": "error"})
        jobs.broadcast({"type": "frames_error", "lecture_id": lecture_id, "course_id": course_id, "error": str(e)})
        raise
