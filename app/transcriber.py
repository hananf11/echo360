"""Async transcription — spawns faster-whisper as a subprocess."""
import asyncio
import json
import logging
import sys

from app import db, jobs

_LOGGER = logging.getLogger(__name__)


async def transcribe_lecture(lecture_id: int, model_name: str = "tiny") -> None:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM lectures WHERE id = ?", [lecture_id]
        ).fetchone()

    if not row:
        return

    if row["audio_status"] != "done" or not row["audio_path"]:
        return

    if row["transcript_status"] == "done":
        return

    try:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'transcribing' WHERE id = ?",
                [lecture_id],
            )
        jobs.broadcast({"type": "transcription_start", "lecture_id": lecture_id})

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.transcribe_worker",
            row["audio_path"], model_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Transcription subprocess failed (rc={proc.returncode}): {stderr.decode()[:500]}")

        segments = json.loads(stdout.decode())

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
                "UPDATE lectures SET transcript_status = 'error' WHERE id = ?",
                [lecture_id],
            )
        jobs.broadcast(
            {"type": "transcription_error", "lecture_id": lecture_id, "error": str(e)}
        )
