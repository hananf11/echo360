"""Download + convert orchestration for the async pipeline."""
import asyncio
import json
import logging
import os
import re
import time

import httpx

from app.database import get_db
from app.models import Lecture
from app import async_downloader, jobs
from app.scraper import _build_driver, _extract_stream_url, _load_session

_LOGGER = logging.getLogger(__name__)

# Throttle progress broadcasts to ~2/sec per lecture
_last_broadcast: dict[int, float] = {}
_BROADCAST_INTERVAL = 0.5  # seconds


def _throttled_progress(lecture_id: int, data: dict, _bcast) -> None:
    """Broadcast progress at most every _BROADCAST_INTERVAL seconds per lecture."""
    now = time.monotonic()
    last = _last_broadcast.get(lecture_id, 0.0)
    if now - last < _BROADCAST_INTERVAL:
        return
    _last_broadcast[lecture_id] = now
    _bcast(data)


def _safe_filename(row) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")[:150]


def _set_status(lecture_id: int, status: str, **extra):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.audio_status = status
            for k, v in extra.items():
                setattr(lec, k, v)


async def run_download(lecture_id: int, output_dir: str) -> None:
    """Main download coroutine — download raw file, then enqueue conversion."""
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec is None:
            raise ValueError(f"Lecture {lecture_id} not found")
        row = lec.to_dict()
        # Also need hostname and course_name from the course
        row["hostname"] = lec.course.hostname
        row["course_name"] = lec.course.name

    course_id = row["course_id"]
    _LOGGER.info(
        "run_download[%d]: %r (%s) → %s",
        lecture_id, row["title"], row["date"], output_dir,
    )

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    # Skip if already done
    if row["audio_status"] == "done" and row["audio_path"] and os.path.exists(row["audio_path"]):
        _LOGGER.info("run_download[%d]: already done, skipping (%s)", lecture_id, row["audio_path"])
        _bcast({"status": "done", "audio_path": row["audio_path"]})
        return

    _set_status(lecture_id, "downloading", error_message=None)
    _bcast({"status": "downloading"})

    os.makedirs(output_dir, exist_ok=True)
    filename = _safe_filename(row)

    video_json = json.loads(row["raw_json"])
    stream_url = _extract_stream_url(video_json, row["hostname"])
    _LOGGER.info(
        "run_download[%d]: stream_url=%s",
        lecture_id,
        (stream_url[0][:80] + "…") if isinstance(stream_url, list) else (str(stream_url or "None")[:80]),
    )

    # Early exit if lecture has no media at all
    if not stream_url:
        lesson = video_json.get("lesson", {})
        has_content = lesson.get("hasContent", False)
        has_media = bool(lesson.get("medias"))
        has_video = lesson.get("hasVideo", False)
        if not has_content and not has_media and not has_video:
            _LOGGER.info(
                "run_download[%d]: no media (hasContent=%s hasMedia=%s hasVideo=%s)",
                lecture_id, has_content, has_media, has_video,
            )
            _set_status(lecture_id, "no_media", error_message="Lecture has no available media")
            _bcast({"status": "no_media"})
            return

    raw_path = None

    if stream_url:
        _LOGGER.info("run_download[%d]: trying fast download path", lecture_id)
        try:
            raw_path = await _download_fast(stream_url, output_dir, filename, lecture_id, _bcast)
            if raw_path:
                _LOGGER.info("run_download[%d]: fast download succeeded → %s", lecture_id, raw_path)
        except Exception:
            _LOGGER.warning("Fast download failed for lecture %d, falling back to Chrome", lecture_id, exc_info=True)
            raw_path = None

    if not raw_path:
        _LOGGER.info("Using Chrome fallback for lecture %d", lecture_id)
        try:
            loop = asyncio.get_running_loop()
            raw_path = await loop.run_in_executor(
                jobs._blocking_executor,
                _download_chrome_fallback, row, output_dir, filename,
            )
            if raw_path:
                _LOGGER.info("run_download[%d]: Chrome fallback succeeded → %s", lecture_id, raw_path)
        except Exception as e:
            _LOGGER.exception("Chrome fallback failed for lecture %d", lecture_id)
            _set_status(lecture_id, "error", error_message=str(e))
            _bcast({"status": "error", "error": str(e)})
            raise

    if not raw_path or not isinstance(raw_path, str):
        if not stream_url:
            # Both fast path and Chrome fallback found no media — mark as terminal
            _set_status(lecture_id, "no_media", error_message="Lecture has no available media")
            _bcast({"status": "no_media"})
        else:
            _set_status(lecture_id, "error", error_message="Download failed — no file produced")
            _bcast({"status": "error", "error": "Download failed — no file produced"})
        return

    # Save raw path and transition to downloaded
    _set_status(lecture_id, "downloaded", raw_path=raw_path)
    _bcast({"status": "downloaded"})

    # Run conversion inline (async subprocess, no thread needed)
    await run_convert(lecture_id, raw_path, output_dir, filename)


async def _download_fast(stream_url, output_dir: str, filename: str, lecture_id: int, _bcast) -> str | None:
    """Download via httpx without Chrome. Returns raw file path or None."""
    from app.scraper import _COOKIES_FILE

    # Build httpx cookies from saved session
    cookies = {}
    if os.path.exists(_COOKIES_FILE):
        with open(_COOKIES_FILE) as f:
            for c in json.load(f):
                cookies[c["name"]] = c["value"]
    _LOGGER.debug("_download_fast[%d]: %d session cookies loaded", lecture_id, len(cookies))

    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as client:
        urls = stream_url if isinstance(stream_url, list) else [stream_url]
        single_url = urls[0]
        is_m3u8 = single_url.endswith(".m3u8")
        _LOGGER.info(
            "_download_fast[%d]: url=%s… type=%s",
            lecture_id, single_url[:80], "m3u8" if is_m3u8 else "direct",
        )

        dl_start = time.monotonic()

        def on_progress(done, total):
            elapsed = time.monotonic() - dl_start
            speed_bps = int(done / elapsed) if elapsed > 0 else 0
            remaining = total - done
            eta = remaining / speed_bps if speed_bps > 0 else None
            progress = {
                "done": done, "total": total, "stage": "download",
                "speed_bps": speed_bps,
            }
            if eta is not None:
                progress["eta_seconds"] = round(eta, 1)
            _throttled_progress(lecture_id, {"status": "downloading", "progress": progress}, _bcast)

        if is_m3u8:
            segments = await async_downloader.resolve_audio_m3u8(client, single_url)
            _LOGGER.info("_download_fast[%d]: resolved %d M3U8 segments", lecture_id, len(segments))
            raw_path = await async_downloader.download_segments(client, segments, output_dir, on_progress)
        else:
            raw_path = await async_downloader.download_direct(client, single_url, output_dir, filename, on_progress)

    elapsed = time.monotonic() - dl_start
    _LOGGER.info("_download_fast[%d]: finished in %.1fs → %s", lecture_id, elapsed, raw_path)
    return raw_path


def _download_signed_hls(session, master_url: str, output_dir: str, filename: str, lecture_id) -> str | None:
    """Download a signed HLS stream, preserving auth query-string on all sub-resource URLs.

    Echo360 live streams use a signed master URL (token in query string) but list
    sub-playlists as relative paths.  Standard urljoin() strips the query string, so
    sub-playlists and segments end up unsigned and get 403.  This function fixes that.
    """
    import concurrent.futures
    import tempfile
    import m3u8 as m3u8_lib
    from urllib.parse import urlparse, urlunparse, urljoin

    def _resolve(parent_url: str, uri: str) -> str:
        """Resolve uri relative to parent_url, preserving parent's query string."""
        if uri.startswith("http"):
            return uri  # already absolute — use as-is
        absolute = urljoin(parent_url, uri)
        parsed_parent = urlparse(parent_url)
        parsed_abs = urlparse(absolute)
        if parsed_parent.query and not parsed_abs.query:
            absolute = urlunparse(parsed_abs._replace(query=parsed_parent.query))
        return absolute

    # Fetch master playlist
    r = session.get(master_url, timeout=20)
    if not r.ok:
        _LOGGER.warning("_download_signed_hls[%s]: master M3U8 returned %d", lecture_id, r.status_code)
        return None

    playlist = m3u8_lib.loads(r.text, uri=master_url)

    # Pick sub-playlist: prefer separate AUDIO track, fall back to last video variant
    sub_url = None
    for media in playlist.media:
        if media.type == "AUDIO" and media.uri:
            sub_url = _resolve(master_url, media.uri)
            break
    if not sub_url and playlist.playlists:
        sub_url = _resolve(master_url, playlist.playlists[-1].uri)
    if not sub_url:
        _LOGGER.warning("_download_signed_hls[%s]: no sub-playlist in master", lecture_id)
        return None

    # Fetch segment-level playlist (handle one level of nesting)
    r = session.get(sub_url, timeout=20)
    if not r.ok:
        _LOGGER.warning("_download_signed_hls[%s]: sub-playlist %d at %s", lecture_id, r.status_code, sub_url[:80])
        return None
    seg_pl = m3u8_lib.loads(r.text, uri=sub_url)
    if not seg_pl.segments and seg_pl.playlists:
        nested_url = _resolve(sub_url, seg_pl.playlists[0].uri)
        r = session.get(nested_url, timeout=20)
        if not r.ok:
            return None
        seg_pl = m3u8_lib.loads(r.text, uri=nested_url)
        sub_url = nested_url

    segment_urls = [_resolve(sub_url, seg.uri) for seg in seg_pl.segments]
    if not segment_urls:
        _LOGGER.warning("_download_signed_hls[%s]: segment list is empty", lecture_id)
        return None

    _LOGGER.info("_download_signed_hls[%s]: downloading %d segments", lecture_id, len(segment_urls))

    def _fetch(args):
        idx, url = args
        for _ in range(3):
            try:
                resp = session.get(url, timeout=30)
                if resp.ok:
                    return idx, resp.content
            except Exception:
                pass
            time.sleep(1)
        return idx, None

    os.makedirs(output_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(dir=output_dir)
    segment_data: dict[int, bytes] = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            for idx, data in pool.map(_fetch, enumerate(segment_urls)):
                if data is None:
                    _LOGGER.warning("_download_signed_hls[%s]: segment %d failed, aborting", lecture_id, idx)
                    return None
                segment_data[idx] = data

        ext = segment_urls[0].split("?")[0].rsplit(".", 1)[-1] if segment_urls else "ts"
        raw_path = os.path.join(output_dir, f"{filename}_raw.{ext}")
        with open(raw_path, "wb") as f:
            for i in range(len(segment_urls)):
                f.write(segment_data[i])
        _LOGGER.info("_download_signed_hls[%s]: wrote %s", lecture_id, raw_path)
        return raw_path
    finally:
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _download_chrome_fallback(row, output_dir: str, filename: str) -> str | None:
    """Blocking Chrome-based download. Runs in a thread executor."""
    from echo360.videos import EchoCloudVideo
    from echo360.hls_downloader import Downloader

    lecture_id = row.get("id", "?")
    _LOGGER.info("_download_chrome_fallback[%s]: starting for %r", lecture_id, row.get("title"))
    video_json = json.loads(row["raw_json"])
    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, row["hostname"]):
            raise RuntimeError("No valid session. Please re-authenticate via the CLI.")

        video = EchoCloudVideo(video_json, driver, row["hostname"], alternative_feeds=False)
        _LOGGER.info("_download_chrome_fallback[%s]: video.url=%r", lecture_id, video.url)

        # EchoCloudVideo sets _url to False when no streams are found via JSON or quick scrape.
        # This can happen when hasAvailableVideo is None/False in the JSON but the media is
        # actually available — the SPA loads the stream URL via XHR after initial render.
        # Poll the classroom page for up to 30s waiting for M3U8 URLs to appear.
        if not video.url:
            _LOGGER.info(
                "_download_chrome_fallback[%s]: no URL from JSON, polling classroom page for M3U8...",
                lecture_id,
            )
            lesson_id = video_json["lesson"]["lesson"]["id"]
            classroom_url = f"{row['hostname']}/lesson/{lesson_id}/classroom"
            driver.get(classroom_url)

            found_url = None
            for attempt in range(15):
                time.sleep(2)
                page = driver.page_source.replace("\\/", "/")
                urls = set(re.findall(r'https://[^,"\'<\s]+\.m3u8', page))
                if urls:
                    av = sorted(u for u in urls if u.endswith("av.m3u8"))
                    ao = sorted(u for u in urls if u.endswith("a.m3u8"))
                    chosen = av or ao or sorted(urls)
                    if chosen:
                        found_url = chosen[-1]
                        break
                _LOGGER.debug(
                    "_download_chrome_fallback[%s]: attempt %d, no m3u8 yet (page=%d chars)",
                    lecture_id, attempt + 1, len(page),
                )

            if not found_url:
                _LOGGER.warning(
                    "_download_chrome_fallback[%s]: classroom page never yielded an M3U8 URL",
                    lecture_id,
                )
                return None

            _LOGGER.info(
                "_download_chrome_fallback[%s]: found M3U8 via polling: %s",
                lecture_id, found_url[:80],
            )
            # Build session with Chrome's current cookies (includes live-domain cookies
            # acquired while the classroom page loaded its video player).
            import requests as _requests
            session = _requests.Session()
            for c in driver.get_cookies():
                session.cookies.set(c["name"], c["value"])
            return _download_signed_hls(session, found_url, output_dir, filename, lecture_id)

        # Normal path: video.url was resolved from JSON — use existing download
        result = video.download(output_dir, filename, audio_only=True)
        if result:
            # Find the raw or opus file produced
            opus_path = os.path.join(output_dir, filename + ".opus")
            if os.path.exists(opus_path):
                return opus_path
            # Check for raw .ts files
            for ext in ("ts", "mp4", "m4s"):
                p = os.path.join(output_dir, f"raw_download.{ext}")
                if os.path.exists(p):
                    return p
        return None
    except RuntimeError:
        raise  # re-raise auth errors so outer handler reports them
    except Exception:
        _LOGGER.exception("Chrome fallback unexpected error for lecture")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def _probe_audio_codec(input_file: str) -> str | None:
    """Async ffprobe to detect the audio codec."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        codec = stdout.decode().strip() or None
        _LOGGER.debug("_probe_audio_codec: %s → %s", os.path.basename(input_file), codec)
        return codec
    except Exception as exc:
        _LOGGER.debug("_probe_audio_codec: failed for %s — %s", os.path.basename(input_file), exc)
        return None


async def _convert_to_opus(
    input_file: str,
    output_file: str,
    duration_seconds: float | None = None,
    on_progress=None,
) -> bool:
    """Async ffmpeg conversion to opus with optional progress reporting."""
    if os.path.exists(output_file):
        os.remove(output_file)

    codec = await _probe_audio_codec(input_file)
    if codec == "opus":
        audio_opts = ["-vn", "-c:a", "copy"]
    else:
        audio_opts = ["-vn", "-c:a", "libopus", "-b:a", "48k", "-threads", "0"]

    use_progress = duration_seconds is not None and duration_seconds > 0 and on_progress is not None

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error",
        *([ "-progress", "pipe:1", "-nostats"] if use_progress else []),
        "-i", input_file,
        *audio_opts,
        output_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    if use_progress:
        async def _read_progress():
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode().strip()
                if text.startswith("out_time_ms="):
                    try:
                        us = int(text.split("=", 1)[1])
                        done_secs = us / 1_000_000
                        on_progress(done_secs, duration_seconds)
                    except (ValueError, ZeroDivisionError):
                        pass

        async def _read_stderr():
            assert proc.stderr is not None
            return await proc.stderr.read()

        stderr_data, _ = await asyncio.gather(_read_stderr(), _read_progress())
        await proc.wait()
    else:
        _, stderr_data = await proc.communicate()

    if proc.returncode != 0:
        _LOGGER.error("ffmpeg failed (rc=%d): %s", proc.returncode, (stderr_data or b"").decode()[:500])
        return False
    return os.path.exists(output_file)


async def run_convert(lecture_id: int, raw_path: str, output_dir: str, filename: str) -> None:
    """Convert raw file to .opus via async subprocess."""
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        course_id = lec.course_id if lec else None
        duration_seconds = lec.duration_seconds if lec else None

    _LOGGER.info(
        "run_convert[%d]: %s → opus (duration=%ss)",
        lecture_id, os.path.basename(raw_path), duration_seconds,
    )

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    _set_status(lecture_id, "converting")
    _bcast({"status": "converting"})

    def _on_convert_progress(done_secs, total_secs):
        _throttled_progress(lecture_id, {
            "status": "converting",
            "progress": {
                "done": round(done_secs, 1),
                "total": round(total_secs, 1),
                "stage": "convert",
            },
        }, _bcast)

    try:
        opus_path = os.path.join(output_dir, filename + ".opus")

        # If Chrome fallback already produced an opus file, just use it
        if raw_path.endswith(".opus"):
            _LOGGER.info("run_convert[%d]: raw file is already opus, skipping conversion", lecture_id)
            _set_status(lecture_id, "done", audio_path=raw_path, raw_path=None)
            _bcast({"status": "done", "audio_path": raw_path})
            return

        convert_start = time.monotonic()
        if await _convert_to_opus(raw_path, opus_path, duration_seconds, _on_convert_progress):
            elapsed = time.monotonic() - convert_start
            size_mb = os.path.getsize(opus_path) / 1024 / 1024
            _LOGGER.info(
                "run_convert[%d]: done in %.1fs → %s (%.1f MB)",
                lecture_id, elapsed, os.path.basename(opus_path), size_mb,
            )
            try:
                os.remove(raw_path)
            except OSError:
                pass
            _set_status(lecture_id, "done", audio_path=opus_path, raw_path=None)
            _bcast({"status": "done", "audio_path": opus_path})
        else:
            _set_status(lecture_id, "error", error_message="ffmpeg conversion failed")
            _bcast({"status": "error", "error": "ffmpeg conversion failed"})
    except Exception as e:
        _LOGGER.exception("Conversion failed for lecture %d", lecture_id)
        _set_status(lecture_id, "error", error_message=str(e))
        _bcast({"status": "error", "error": str(e)})
