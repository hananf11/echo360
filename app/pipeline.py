"""Download + convert orchestration for the async pipeline."""
import asyncio
import json
import logging
import os
import re
import time

import httpx

from app import async_downloader, db, jobs
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
    with db.get_db() as conn:
        sets = ["audio_status = ?"]
        vals = [status]
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(lecture_id)
        conn.execute(f"UPDATE lectures SET {', '.join(sets)} WHERE id = ?", vals)


async def run_download(lecture_id: int, output_dir: str) -> None:
    """Main download coroutine — download raw file, then enqueue conversion."""
    with db.get_db() as conn:
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

    course_id = row["course_id"]

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    # Skip if already done
    if row["audio_status"] == "done" and row["audio_path"] and os.path.exists(row["audio_path"]):
        _bcast({"status": "done", "audio_path": row["audio_path"]})
        return

    _set_status(lecture_id, "downloading", error_message=None)
    _bcast({"status": "downloading"})

    os.makedirs(output_dir, exist_ok=True)
    filename = _safe_filename(row)

    video_json = json.loads(row["raw_json"])
    stream_url = _extract_stream_url(video_json, row["hostname"])

    raw_path = None

    if stream_url:
        try:
            raw_path = await _download_fast(stream_url, output_dir, filename, lecture_id, _bcast)
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
        except Exception as e:
            _LOGGER.exception("Chrome fallback failed for lecture %d", lecture_id)
            _set_status(lecture_id, "error", error_message=str(e))
            _bcast({"status": "error", "error": str(e)})
            raise

    if not raw_path or not isinstance(raw_path, str):
        msg = "No stream URL found — lecture may not have available media" if not stream_url else "Download failed — no file produced"
        _set_status(lecture_id, "error", error_message=msg)
        _bcast({"status": "error", "error": msg})
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

    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True) as client:
        urls = stream_url if isinstance(stream_url, list) else [stream_url]
        single_url = urls[0]

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

        if single_url.endswith(".m3u8"):
            segments = await async_downloader.resolve_audio_m3u8(client, single_url)
            raw_path = await async_downloader.download_segments(client, segments, output_dir, on_progress)
        else:
            raw_path = await async_downloader.download_direct(client, single_url, output_dir, filename, on_progress)

    return raw_path


def _download_chrome_fallback(row, output_dir: str, filename: str) -> str | None:
    """Blocking Chrome-based download. Runs in a thread executor."""
    from echo360.videos import EchoCloudVideo
    from echo360.hls_downloader import Downloader, urljoin
    from echo360.naive_m3u8_parser import NaiveM3U8Parser

    video_json = json.loads(row["raw_json"])
    driver = None
    try:
        driver = _build_driver()
        if not _load_session(driver, row["hostname"]):
            raise RuntimeError("No valid session. Please re-authenticate via the CLI.")

        video = EchoCloudVideo(video_json, driver, row["hostname"], alternative_feeds=False)
        # EchoCloudVideo sets _url to False when no streams are found
        if not video.url:
            _LOGGER.warning("Chrome fallback: no stream URL found for lecture (video.url=%r)", video.url)
            return None
        # Use the existing download which produces a raw .ts file (we skip conversion here)
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
        return stdout.decode().strip() or None
    except Exception:
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
    with db.get_db() as conn:
        row = conn.execute("SELECT course_id, duration_seconds FROM lectures WHERE id = ?", [lecture_id]).fetchone()
    course_id = row["course_id"] if row else None
    duration_seconds = row["duration_seconds"] if row else None

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
            _set_status(lecture_id, "done", audio_path=raw_path, raw_path=None)
            _bcast({"status": "done", "audio_path": raw_path})
            return

        if await _convert_to_opus(raw_path, opus_path, duration_seconds, _on_convert_progress):
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
