"""Sync lecture transcripts and notes to Outline wiki."""
import json
import logging
import re
import threading
from datetime import datetime, timezone, timedelta

_NZDT = timezone(timedelta(hours=13))  # NZDT (UTC+13); NZST is UTC+12


def _fmt_nz_date(utc_str: str | None) -> str | None:
    """Convert a UTC datetime string to a NZ-localised date string."""
    if not utc_str:
        return None
    try:
        dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        nz = dt.astimezone(_NZDT)
        return nz.strftime("%-d %B %Y %H:%M")
    except ValueError:
        return utc_str

from app.outline import (
    OUTLINE_API_KEY,
    OUTLINE_COLLECTION,
    find_or_create_collection,
    find_or_create_document,
    list_documents,
    create_document,
    update_document,
    get_document,
)
from app.database import get_db
from app.models import Course, Lecture, Note, Transcript

_LOGGER = logging.getLogger(__name__)

# Prevent concurrent syncs from racing on hierarchy creation (year/course docs)
_SYNC_LOCK = threading.Lock()

# Cache doc IDs so we never re-query Outline for year/course docs after creation.
# Key: (collection_id, parent_doc_id_or_None, title) → doc dict
_DOC_CACHE: dict[tuple, dict] = {}

# Target length (in characters) for merged transcript lines
_MERGE_TARGET_LEN = 200
# Hard max — flush even without sentence-ending punctuation
_MERGE_MAX_LEN = 400


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

        # Flush when we hit the target length and the text ends a sentence,
        # or unconditionally at the hard max (handles unpunctuated transcripts)
        ends_sentence = text[-1] in ".!?…"
        if (buf_len >= _MERGE_TARGET_LEN and ends_sentence) or buf_len >= _MERGE_MAX_LEN:
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


_TRACKER_TITLE = "Action Items"
_TRACKER_HEADER = (
    "| Status | Task | Course | Lecture | Due Date |\n"
    "| --- | --- | --- | --- | --- |\n"
)


def _tracker_row_key(course: str, lecture_date: str, task: str) -> str:
    """Normalised dedup key for a tracker row."""
    return f"{lecture_date}|{course.strip().lower()}|{task.strip().lower()}"


def _parse_tracker_keys(text: str) -> set[str]:
    """Parse all existing row keys from the tracker table.

    Each data row is expected to be: | status | task | course | lecture | due_date |
    We rebuild the key from columns 2 (task), 3 (course), 4 (lecture/date).
    The lecture column contains the lecture title which starts with the date.
    """
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| Status") or re.match(r"^\|\s*---", line):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 5:
            continue
        # cols: [status, task, course, lecture, due_date]
        task = cols[1]
        course = cols[2]
        lecture_col = cols[3]  # e.g. "2026-02-18 - Lecture 3 - ..."
        lecture_date = lecture_col[:10] if len(lecture_col) >= 10 else lecture_col
        keys.add(_tracker_row_key(course, lecture_date, task))
    return keys


def _build_tracker_row(task: str, due_date: str | None, course: str, lecture_title: str) -> str:
    return f"| Todo | {task} | {course} | {lecture_title} | {due_date or ''} |\n"


def _sync_action_items_tracker(
    action_items: list[dict],
    course_title: str,
    lecture_date: str,
    lecture_title: str,
    collection_id: str,
    year_doc_id: str,
) -> None:
    """Merge new action items into the shared tracker doc.

    Finds or creates a top-level "Action Items" document in the collection.
    Existing rows are never modified — only new rows are appended — so any
    Status changes made by the user in Outline are preserved across re-syncs.
    """
    if not action_items:
        return

    tracker_doc = find_or_create_document(
        _TRACKER_TITLE, collection_id,
        parent_document_id=year_doc_id,
        text=_TRACKER_HEADER,
    )
    doc_data = get_document(tracker_doc["id"])
    existing_text: str = doc_data.get("text", "") or ""

    # Ensure the header is present (handles empty/fresh docs)
    if "| Status |" not in existing_text:
        existing_text = _TRACKER_HEADER

    existing_keys = _parse_tracker_keys(existing_text)

    new_rows: list[str] = []
    for item in action_items:
        task = item.get("task", "").strip()
        if not task:
            continue
        key = _tracker_row_key(course_title, lecture_date, task)
        if key in existing_keys:
            continue
        new_rows.append(_build_tracker_row(task, item.get("due_date"), course_title, lecture_title))

    if not new_rows:
        _LOGGER.debug("No new action items to add to tracker for lecture %s", lecture_title)
        return

    updated_text = existing_text.rstrip("\n") + "\n" + "".join(new_rows)
    update_document(tracker_doc["id"], text=updated_text)
    _LOGGER.info("Added %d action item(s) to tracker from %s", len(new_rows), lecture_title)


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
        action_items: list[dict] = []
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
                notes_date = _fmt_nz_date(note.created_at)
                generated_title = note.generated_title
                action_items = json.loads(note.action_items) if note.action_items else []

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
                transcript_date = _fmt_nz_date(transcript.created_at)

    # Walk the hierarchy: collection → year → course → lecture → transcript
    # Lock + cache prevents concurrent syncs from racing to create duplicate year/course docs
    with _SYNC_LOCK:
        collection = find_or_create_collection(OUTLINE_COLLECTION)
        collection_id = collection["id"]

        year_key = (collection_id, None, year)
        if year_key not in _DOC_CACHE:
            _DOC_CACHE[year_key] = find_or_create_document(year, collection_id)
        year_doc = _DOC_CACHE[year_key]

        course_key = (collection_id, year_doc["id"], course_title)
        if course_key not in _DOC_CACHE:
            _DOC_CACHE[course_key] = find_or_create_document(
                course_title, collection_id, parent_document_id=year_doc["id"],
            )
        course_doc = _DOC_CACHE[course_key]

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
        # Always update the title (may have gained a generated title).
        # Only overwrite body if we actually have generated content — avoids
        # wiping user-added content when sync runs after transcription but
        # before note generation.
        update_kwargs: dict = {"title": lecture_title}
        if lecture_body:
            update_kwargs["text"] = lecture_body
        update_document(lecture_doc["id"], **update_kwargs)

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

    # Merge action items into the shared tracker (idempotent — won't overwrite user edits)
    if action_items:
        _sync_action_items_tracker(
            action_items,
            course_title=course_title,
            lecture_date=lecture_date,
            lecture_title=lecture_title,
            collection_id=collection_id,
            year_doc_id=year_doc["id"],
        )

    _LOGGER.info("Synced lecture %d to Outline: %s", lecture_id, lecture_title)
