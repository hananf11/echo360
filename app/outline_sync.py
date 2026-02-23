"""Sync lecture transcripts and notes to Outline wiki."""
import json
import logging

from app.outline import (
    OUTLINE_API_KEY,
    OUTLINE_COLLECTION,
    find_or_create_collection,
    find_or_create_document,
    list_documents,
    create_document,
    update_document,
)
from app.database import get_db
from app.models import Course, Lecture, Note, Transcript

_LOGGER = logging.getLogger(__name__)

# Target length (in characters) for merged transcript lines
_MERGE_TARGET_LEN = 200


def _merge_transcript_segments(segments: list[dict]) -> list[dict]:
    """Merge short transcript segments into longer sentence groups.

    Combines consecutive segments until the merged text reaches roughly
    _MERGE_TARGET_LEN characters or a sentence-ending punctuation is hit.
    Each merged group keeps the start time of the first segment.
    """
    if not segments:
        return []

    merged: list[dict] = []
    buf_start = segments[0]["start"]
    buf_texts: list[str] = []
    buf_len = 0

    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        if not buf_texts:
            buf_start = seg["start"]

        buf_texts.append(text)
        buf_len += len(text)

        # Flush when we hit the target length and the text ends a sentence
        ends_sentence = text[-1] in ".!?…"
        if buf_len >= _MERGE_TARGET_LEN and ends_sentence:
            merged.append({"start": buf_start, "text": " ".join(buf_texts)})
            buf_texts = []
            buf_len = 0

    # Flush remainder
    if buf_texts:
        merged.append({"start": buf_start, "text": " ".join(buf_texts)})

    return merged


def _format_transcript_md(segments: list[dict]) -> str:
    """Merge and format transcript segments as timestamped markdown."""
    merged = _merge_transcript_segments(segments)
    lines = []
    for seg in merged:
        mins = int(seg["start"] // 60)
        secs = int(seg["start"] % 60)
        lines.append(f"**[{mins:02d}:{secs:02d}]** {seg['text']}")
    return "\n\n".join(lines)


def _generation_info(
    *,
    transcript_model: str | None = None,
    transcript_date: str | None = None,
    notes_model: str | None = None,
    notes_date: str | None = None,
) -> str:
    """Build a small metadata block showing how content was generated."""
    parts = []
    if transcript_model:
        line = f"Transcribed with `{transcript_model}`"
        if transcript_date:
            line += f" on {transcript_date}"
        parts.append(line)
    if notes_model:
        line = f"Notes generated with `{notes_model}`"
        if notes_date:
            line += f" on {notes_date}"
        parts.append(line)
    if not parts:
        return ""
    return "---\n*" + " · ".join(parts) + "*\n"


def sync_lecture_to_outline(lecture_id: int) -> None:
    """Push lecture transcript and/or notes to Outline wiki.

    Hierarchy: Collection → Year doc → Course doc → Lecture doc → Transcript doc

    Idempotent — safe to call multiple times. Silently returns if
    OUTLINE_API_KEY is not set. Never raises — logs all errors.
    """
    if not OUTLINE_API_KEY:
        return

    try:
        _sync(lecture_id)
    except Exception:
        _LOGGER.exception("Outline sync failed for lecture %d", lecture_id)


def _sync(lecture_id: int) -> None:
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            return
        course = session.get(Course, lec.course_id)
        if not course:
            return

        lecture_date = lec.date or "1970-01-01"
        year = lecture_date[:4]
        course_title = course.display_name or course.name

        has_notes = lec.notes_status == "done"
        has_transcript = lec.transcript_status == "done"

        notes_md = ""
        notes_model = None
        notes_date = None
        generated_title = None
        if has_notes:
            note = (
                session.query(Note)
                .filter(Note.lecture_id == lecture_id)
                .order_by(Note.id.desc())
                .first()
            )
            if note:
                notes_md = note.content_md
                notes_model = note.model
                notes_date = note.created_at
                generated_title = note.generated_title

        # Build lecture title: "2026-02-18 - Lecture 3 - Generated Title"
        base_title = lec.title  # e.g. "Lecture 3"
        if generated_title:
            lecture_title = f"{lecture_date} - {base_title} - {generated_title}"
        else:
            lecture_title = f"{lecture_date} - {base_title}"

        transcript_segments: list[dict] = []
        transcript_model = None
        transcript_date = None
        if has_transcript:
            transcript = (
                session.query(Transcript)
                .filter(Transcript.lecture_id == lecture_id)
                .order_by(Transcript.id.desc())
                .first()
            )
            if transcript:
                transcript_segments = json.loads(transcript.segments)
                transcript_model = transcript.model
                transcript_date = transcript.created_at

    if not has_notes and not has_transcript:
        return

    # Walk the hierarchy: collection → year → course → lecture → transcript
    collection = find_or_create_collection(OUTLINE_COLLECTION)
    collection_id = collection["id"]

    year_doc = find_or_create_document(year, collection_id)
    course_doc = find_or_create_document(
        course_title, collection_id, parent_document_id=year_doc["id"],
    )

    # Build lecture doc body: notes + generation metadata footer
    lecture_body = notes_md or ""
    info_footer = _generation_info(
        transcript_model=transcript_model,
        transcript_date=transcript_date,
        notes_model=notes_model,
        notes_date=notes_date,
    )
    if info_footer:
        lecture_body = lecture_body.rstrip() + "\n\n" + info_footer if lecture_body else info_footer

    # Find lecture doc by date prefix (title may change when generated title is added)
    date_prefix = f"{lecture_date} - "
    lecture_doc = None
    existing_docs = list_documents(collection_id, parent_document_id=course_doc["id"])
    for doc in existing_docs:
        if doc.get("title", "").startswith(date_prefix):
            lecture_doc = doc
            break

    if lecture_doc is None:
        lecture_doc = create_document(
            lecture_title, lecture_body, collection_id,
            parent_document_id=course_doc["id"],
        )
    else:
        # Update title (may have changed) and body
        update_document(lecture_doc["id"], title=lecture_title, text=lecture_body)

    # Upsert transcript child doc (merged segments + metadata)
    if has_transcript and transcript_segments:
        transcript_md = _format_transcript_md(transcript_segments)
        transcript_info = _generation_info(
            transcript_model=transcript_model,
            transcript_date=transcript_date,
        )
        if transcript_info:
            transcript_md = transcript_md.rstrip() + "\n\n" + transcript_info

        transcript_doc = find_or_create_document(
            "Transcript", collection_id,
            parent_document_id=lecture_doc["id"],
            text=transcript_md,
        )
        update_document(transcript_doc["id"], text=transcript_md)

    _LOGGER.info("Synced lecture %d to Outline: %s", lecture_id, lecture_title)
