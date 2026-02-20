"""Transcription worker using faster-whisper."""
import json

from app import db, jobs


def transcribe_lecture(lecture_id: int, model_name: str = "turbo") -> None:
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

        from faster_whisper import WhisperModel

        model = WhisperModel(model_name, device="auto", compute_type="int8")
        segments_iter, _ = model.transcribe(row["audio_path"])
        segments = [
            {"start": s.start, "end": s.end, "text": s.text.strip()}
            for s in segments_iter
        ]

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
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'error' WHERE id = ?",
                [lecture_id],
            )
        jobs.broadcast(
            {"type": "transcription_error", "lecture_id": lecture_id, "error": str(e)}
        )
