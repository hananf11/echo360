"""Async transcription — local faster-whisper or remote Groq API."""
import asyncio
import json
import logging
import os
import sys

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    before_sleep_log,
)

from app.database import get_db
from app.models import Lecture, Transcript
from app import jobs

_LOGGER = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


class _RetryableAPIError(Exception):
    """Raised for transient API errors that should be retried."""
    def __init__(self, status_code: int, retry_after: float | None = None, text: str = ""):
        self.status_code = status_code
        self.retry_after = retry_after
        self.text = text
        super().__init__(f"API error ({status_code}): {text[:200]}")


def _groq_wait(retry_state) -> float:
    """Custom wait: respect Retry-After on 429, exponential backoff on 5xx."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableAPIError):
        if exc.status_code == 429:
            return (int(exc.retry_after) + 5) if exc.retry_after else 60
        return min(2 ** (retry_state.attempt_number - 1) * 5, 120)
    return 5


def _modal_wait(retry_state) -> float:
    """Exponential backoff for Modal: 10s, 20s, 40s, 80s."""
    return min(2 ** (retry_state.attempt_number - 1) * 10, 80)


def _parse_segments(data: dict) -> list[dict]:
    """Extract segment list from a Whisper API response."""
    return [
        {"start": seg["start"], "end": seg["end"], "text": seg["text"].strip()}
        for seg in data.get("segments", [])
    ]


async def transcribe_lecture(lecture_id: int, model_name: str = "groq") -> None:
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            return
        if lec.audio_status != "done" or not lec.audio_path:
            return
        if lec.transcript_status == "done":
            return
        course_id = lec.course_id
        audio_path = lec.audio_path
        duration_seconds = lec.duration_seconds

    def _bcast(data: dict):
        jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, **data})

    try:
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.transcript_status = "transcribing"
                lec.error_message = None
        jobs.broadcast({"type": "transcription_start", "lecture_id": lecture_id})
        _bcast({"status": "transcribing"})

        if model_name == "cloud":
            segments = await _transcribe_cloud(audio_path, _bcast)
        elif model_name.startswith("groq"):
            segments = await _transcribe_groq(audio_path, model_name, _bcast)
        elif model_name.startswith("modal"):
            segments = await _transcribe_modal(audio_path, _bcast)
        else:
            segments = await _transcribe_local(audio_path, model_name)

        with get_db() as session:
            session.add(Transcript(
                lecture_id=lecture_id,
                model=model_name,
                segments=json.dumps(segments),
            ))
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.transcript_status = "done"
                lec.transcript_model = model_name

        jobs.broadcast({"type": "transcription_done", "lecture_id": lecture_id})

    except Exception as e:
        _LOGGER.exception("Transcription failed for lecture %d", lecture_id)
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.transcript_status = "error"
                lec.error_message = str(e)[:500]
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


async def _transcribe_groq(audio_path: str, model_name: str, _bcast) -> list[dict]:
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

    if needs_chunking:
        _LOGGER.info("Audio file is %.1f MB, splitting into chunks for Groq API", file_size / 1024 / 1024)
        chunks = await _split_audio(audio_path)
        try:
            return await _transcribe_groq_chunked(chunks, groq_model, api_key)
        finally:
            # Clean up temp chunk files
            import shutil
            shutil.rmtree(os.path.dirname(chunks[0]), ignore_errors=True)
    else:
        return await _transcribe_groq_single(audio_path, groq_model, api_key)


@retry(
    retry=retry_if_exception_type(_RetryableAPIError),
    wait=_groq_wait,
    stop=stop_after_attempt(20),
    before_sleep=before_sleep_log(_LOGGER, logging.WARNING),
    reraise=True,
)
async def _transcribe_groq_single(audio_path: str, groq_model: str, api_key: str) -> list[dict]:
    """Transcribe a single file via Groq API with retry for transient errors."""
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
        return _parse_segments(resp.json())
    if resp.status_code == 429 or resp.status_code >= 500:
        retry_after = float(resp.headers["retry-after"]) if resp.headers.get("retry-after") else None
        raise _RetryableAPIError(resp.status_code, retry_after, resp.text[:500])
    raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text[:500]}")


async def _transcribe_groq_chunked(chunks: list[str], groq_model: str, api_key: str) -> list[dict]:
    """Transcribe multiple chunks sequentially via Groq, stitching timestamps."""
    all_segments = []
    time_offset = 0.0

    for i, chunk_path in enumerate(chunks):
        _LOGGER.info("Transcribing chunk %d/%d", i + 1, len(chunks))
        chunk_segments = await _transcribe_groq_single(chunk_path, groq_model, api_key)

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

    return all_segments


async def _transcribe_modal(audio_path: str, _bcast) -> list[dict]:
    """Transcribe via a self-hosted Modal serverless endpoint."""
    endpoint_url = os.environ.get("MODAL_WHISPER_URL")
    if not endpoint_url:
        raise RuntimeError("MODAL_WHISPER_URL environment variable is not set. Deploy modal_whisper.py first.")

    return await _transcribe_modal_request(audio_path, endpoint_url)


@retry(
    retry=retry_if_exception_type((_RetryableAPIError, httpx.ReadError, httpx.ConnectError, httpx.TimeoutException)),
    wait=_modal_wait,
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(_LOGGER, logging.WARNING),
    reraise=True,
)
async def _transcribe_modal_request(audio_path: str, endpoint_url: str) -> list[dict]:
    """Single Modal API call — retried by tenacity on transient errors."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0), follow_redirects=True) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                endpoint_url,
                files={"file": (os.path.basename(audio_path), f, "audio/ogg")},
            )

    if resp.status_code == 200:
        return _parse_segments(resp.json())
    if resp.status_code == 303 or resp.status_code >= 500:
        raise _RetryableAPIError(resp.status_code, text=resp.text[:500])
    raise RuntimeError(f"Modal endpoint error ({resp.status_code}): {resp.text[:500]}")


async def _transcribe_cloud(audio_path: str, _bcast) -> list[dict]:
    """Cloud auto mode: try Groq first, fall back to Modal on rate limit or error."""
    groq_key = os.environ.get("GROQ_API_KEY")
    modal_url = os.environ.get("MODAL_WHISPER_URL")

    if groq_key:
        try:
            return await _transcribe_groq(audio_path, "groq", _bcast)
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

    return await _transcribe_modal(audio_path, _bcast)


async def _transcribe_local(audio_path: str, model_name: str) -> list[dict]:
    """Transcribe locally via faster-whisper subprocess."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.transcribe_worker",
        audio_path, model_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_data, stderr_data = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"Transcription subprocess failed (rc={proc.returncode}): {stderr_data.decode()[:500]}")

    return json.loads(stdout_data.decode())
