"""Async transcription — local faster-whisper or remote Groq API."""
import asyncio
import json
import logging
import os
import sys
import time

import httpx

from app import db, jobs

_LOGGER = logging.getLogger(__name__)

_BROADCAST_INTERVAL = 0.5  # seconds

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


async def transcribe_lecture(lecture_id: int, model_name: str = "groq") -> None:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT l.*, l.course_id, l.duration_seconds FROM lectures l WHERE l.id = ?",
            [lecture_id],
        ).fetchone()

    if not row:
        return

    if row["audio_status"] != "done" or not row["audio_path"]:
        return

    if row["transcript_status"] == "done":
        return

    course_id = row["course_id"]
    duration_seconds = row["duration_seconds"]

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    try:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'transcribing', error_message = NULL WHERE id = ?",
                [lecture_id],
            )
        jobs.broadcast({"type": "transcription_start", "lecture_id": lecture_id})
        _bcast({"status": "transcribing"})

        if model_name == "cloud":
            segments = await _transcribe_cloud(row["audio_path"], duration_seconds, _bcast)
        elif model_name.startswith("groq"):
            segments = await _transcribe_groq(row["audio_path"], model_name, duration_seconds, _bcast)
        elif model_name.startswith("modal"):
            segments = await _transcribe_modal(row["audio_path"], duration_seconds, _bcast)
        else:
            segments = await _transcribe_local(row["audio_path"], model_name, duration_seconds, _bcast)

        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO transcripts (lecture_id, model, segments) VALUES (?, ?, ?)",
                [lecture_id, model_name, json.dumps(segments)],
            )
            conn.execute(
                "UPDATE lectures SET transcript_status = 'done', transcript_model = ? WHERE id = ?",
                [model_name, lecture_id],
            )

        jobs.broadcast({"type": "transcription_done", "lecture_id": lecture_id})

    except Exception as e:
        _LOGGER.exception("Transcription failed for lecture %d", lecture_id)
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'error', error_message = ? WHERE id = ?",
                [str(e)[:500], lecture_id],
            )
        jobs.broadcast(
            {"type": "transcription_error", "lecture_id": lecture_id, "error": str(e)}
        )


async def _split_audio(audio_path: str, max_size: int = GROQ_MAX_FILE_SIZE) -> list[str]:
    """Split audio into chunks that stay under max_size bytes. Returns list of chunk file paths."""
    import tempfile

    # Calculate chunk duration based on file size and total duration
    file_size = os.path.getsize(audio_path)
    # Get total duration via ffprobe
    probe = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await probe.communicate()
    total_duration = float(stdout.decode().strip())

    # Target 20 MB per chunk (leave headroom below the 25 MB limit)
    target_chunk_size = 20 * 1024 * 1024
    bytes_per_second = file_size / total_duration
    chunk_seconds = int(target_chunk_size / bytes_per_second)
    chunk_seconds = max(60, chunk_seconds)  # at least 1 minute

    _LOGGER.info("Splitting %.0fs audio into ~%ds chunks (%.0f KB/s bitrate)", total_duration, chunk_seconds, bytes_per_second / 1024)

    chunk_dir = tempfile.mkdtemp(prefix="groq_chunks_")
    chunk_pattern = os.path.join(chunk_dir, "chunk_%03d.ogg")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", audio_path,
        "-f", "segment", "-segment_time", str(chunk_seconds),
        "-c", "copy", "-vn",
        chunk_pattern,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg split failed: {stderr.decode()[:500]}")

    chunks = sorted(
        os.path.join(chunk_dir, f) for f in os.listdir(chunk_dir) if f.startswith("chunk_")
    )
    if not chunks:
        raise RuntimeError("ffmpeg produced no chunks")
    return chunks


async def _transcribe_groq(audio_path: str, model_name: str, duration_seconds: float, _bcast) -> list[dict]:
    """Transcribe via the Groq Whisper API, with automatic chunking for large files."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY environment variable is not set")

    # Parse model variant: "groq" → default, "groq:whisper-large-v3" → specific model
    groq_model = "whisper-large-v3-turbo"
    if ":" in model_name:
        groq_model = model_name.split(":", 1)[1]

    file_size = os.path.getsize(audio_path)
    needs_chunking = file_size > GROQ_MAX_FILE_SIZE

    _bcast({"status": "transcribing", "progress": {"done": 0, "total": round(duration_seconds, 1) if duration_seconds else 0, "stage": "transcribe"}})

    if needs_chunking:
        _LOGGER.info("Audio file is %.1f MB, splitting into chunks for Groq API", file_size / 1024 / 1024)
        chunks = await _split_audio(audio_path)
        try:
            return await _transcribe_groq_chunked(chunks, groq_model, api_key, duration_seconds, _bcast)
        finally:
            # Clean up temp chunk files
            import shutil
            shutil.rmtree(os.path.dirname(chunks[0]), ignore_errors=True)
    else:
        return await _transcribe_groq_single(audio_path, groq_model, api_key, duration_seconds, _bcast)


async def _transcribe_groq_single(audio_path: str, groq_model: str, api_key: str, duration_seconds: float, _bcast) -> list[dict]:
    """Transcribe a single file via Groq API with retry for transient errors."""
    max_retries = 20  # generous limit to handle rate limiting waits
    for attempt in range(max_retries):
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            with open(audio_path, "rb") as f:
                resp = await client.post(
                    GROQ_API_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (os.path.basename(audio_path), f, "audio/ogg")},
                    data={
                        "model": groq_model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                    },
                )

        if resp.status_code == 200:
            break
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after")
            wait = int(float(retry_after)) + 5 if retry_after else 60
            _LOGGER.warning("Groq rate limited, waiting %ds before retry (Retry-After: %s)", wait, retry_after)
            await asyncio.sleep(wait)
            continue
        if resp.status_code >= 500 and attempt < max_retries - 1:
            wait = 2 ** attempt * 5  # 5s, 10s, 20s
            _LOGGER.warning("Groq API returned %d, retrying in %ds (attempt %d/%d)", resp.status_code, wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)
            continue
        raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    segments = []
    for seg in data.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })

    if duration_seconds:
        _bcast({"status": "transcribing", "progress": {"done": round(duration_seconds, 1), "total": round(duration_seconds, 1), "stage": "transcribe"}})

    return segments


async def _transcribe_groq_chunked(chunks: list[str], groq_model: str, api_key: str, duration_seconds: float, _bcast) -> list[dict]:
    """Transcribe multiple chunks sequentially via Groq, stitching timestamps."""
    all_segments = []
    time_offset = 0.0

    for i, chunk_path in enumerate(chunks):
        _LOGGER.info("Transcribing chunk %d/%d", i + 1, len(chunks))
        chunk_segments = await _transcribe_groq_single(chunk_path, groq_model, api_key, duration_seconds, lambda _: None)

        # Offset timestamps by the cumulative duration of previous chunks
        for seg in chunk_segments:
            all_segments.append({
                "start": round(seg["start"] + time_offset, 2),
                "end": round(seg["end"] + time_offset, 2),
                "text": seg["text"],
            })

        # Get the actual duration of this chunk via ffprobe for accurate offset
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", chunk_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await probe.communicate()
        chunk_duration = float(stdout.decode().strip())
        time_offset += chunk_duration

        # Broadcast progress
        if duration_seconds:
            _bcast({"status": "transcribing", "progress": {"done": round(time_offset, 1), "total": round(duration_seconds, 1), "stage": "transcribe"}})

    return all_segments


async def _transcribe_modal(audio_path: str, duration_seconds: float, _bcast) -> list[dict]:
    """Transcribe via a self-hosted Modal serverless endpoint."""
    endpoint_url = os.environ.get("MODAL_WHISPER_URL")
    if not endpoint_url:
        raise RuntimeError("MODAL_WHISPER_URL environment variable is not set. Deploy modal_whisper.py first.")

    _bcast({"status": "transcribing", "progress": {"done": 0, "total": round(duration_seconds, 1) if duration_seconds else 0, "stage": "transcribe"}})

    max_retries = 5
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900.0), follow_redirects=True) as client:
                with open(audio_path, "rb") as f:
                    resp = await client.post(
                        endpoint_url,
                        files={"file": (os.path.basename(audio_path), f, "audio/ogg")},
                    )
        except (httpx.ReadError, httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 10  # 10s, 20s, 40s, 80s
                _LOGGER.warning("Modal connection error (%s), retrying in %ds (attempt %d/%d)", type(e).__name__, wait, attempt + 1, max_retries)
                await asyncio.sleep(wait)
                continue
            raise RuntimeError(f"Modal endpoint connection failed after {max_retries} attempts: {e}")

        if resp.status_code == 200:
            break
        if (resp.status_code == 303 or resp.status_code >= 500) and attempt < max_retries - 1:
            wait = 2 ** attempt * 10
            _LOGGER.warning("Modal endpoint returned %d, retrying in %ds (attempt %d/%d)", resp.status_code, wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)
            continue
        raise RuntimeError(f"Modal endpoint error ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    segments = []
    for seg in data.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })

    if duration_seconds:
        _bcast({"status": "transcribing", "progress": {"done": round(duration_seconds, 1), "total": round(duration_seconds, 1), "stage": "transcribe"}})

    return segments


async def _transcribe_cloud(audio_path: str, duration_seconds: float, _bcast) -> list[dict]:
    """Cloud auto mode: try Groq first, fall back to Modal on rate limit or error."""
    groq_key = os.environ.get("GROQ_API_KEY")
    modal_url = os.environ.get("MODAL_WHISPER_URL")

    if groq_key:
        try:
            return await _transcribe_groq(audio_path, "groq", duration_seconds, _bcast)
        except RuntimeError as e:
            err_msg = str(e)
            if "429" in err_msg or "rate" in err_msg.lower():
                _LOGGER.warning("Groq rate limited in cloud mode, falling back to Modal")
            else:
                _LOGGER.warning("Groq failed in cloud mode (%s), falling back to Modal", err_msg[:200])
            if not modal_url:
                raise
    elif not modal_url:
        raise RuntimeError("Cloud mode requires GROQ_API_KEY and/or MODAL_WHISPER_URL to be set")

    return await _transcribe_modal(audio_path, duration_seconds, _bcast)


async def _transcribe_local(audio_path: str, model_name: str, duration_seconds: float, _bcast) -> list[dict]:
    """Transcribe locally via faster-whisper subprocess."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.transcribe_worker",
        audio_path, model_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _collect_stdout():
        assert proc.stdout is not None
        return await proc.stdout.read()

    async def _stream_stderr():
        assert proc.stderr is not None
        last_broadcast = 0.0
        all_stderr = []
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode().strip()
            if text.startswith("PROGRESS:"):
                try:
                    done_secs = float(text.split(":", 1)[1])
                    now = time.monotonic()
                    if now - last_broadcast >= _BROADCAST_INTERVAL:
                        last_broadcast = now
                        progress = {"done": round(done_secs, 1), "total": round(duration_seconds, 1), "stage": "transcribe"} if duration_seconds else {"done": round(done_secs, 1), "total": 0, "stage": "transcribe"}
                        _bcast({"status": "transcribing", "progress": progress})
                except (ValueError, TypeError):
                    pass
            else:
                all_stderr.append(text)
        return "\n".join(all_stderr)

    stdout_data, stderr_text = await asyncio.gather(_collect_stdout(), _stream_stderr())
    await proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"Transcription subprocess failed (rc={proc.returncode}): {stderr_text[:500]}")

    return json.loads(stdout_data.decode())
