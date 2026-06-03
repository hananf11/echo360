"""Sync lecture transcripts and notes to a private GitHub repository.

Files are written as Markdown under:
  <repo>/<year>/<course_title>/<lecture_title>.md
  <repo>/<year>/<course_title>/<lecture_title>_transcript.md

Environment variables:
  GITHUB_NOTES_REPO  — SSH or HTTPS URL of the private repo (required to enable)
  GITHUB_NOTES_DIR   — local clone path (default: ~/echo360-notes-repo)
"""
import json
import logging
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.database import get_db
from app.models import Course, Lecture, Note, Transcript
from app.outline_sync import (
    _fmt_nz_date,
    _format_transcript_md,
    _generation_info,
)

_LOGGER = logging.getLogger(__name__)

GITHUB_NOTES_REPO = os.environ.get("GITHUB_NOTES_REPO", "")
GITHUB_NOTES_DIR = Path(
    os.environ.get("GITHUB_NOTES_DIR", os.path.expanduser("~/echo360-notes-repo"))
)

_GIT_LOCK = threading.Lock()


def _safe_filename(name: str) -> str:
    """Sanitise a string for use as a filesystem path component."""
    return re.sub(r'[<>:"/\\|?*]', "-", name).strip()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _ensure_repo() -> bool:
    """Clone the repo if it doesn't exist; pull latest if it does.
    Returns True on success."""
    if not GITHUB_NOTES_REPO:
        return False
    try:
        if not GITHUB_NOTES_DIR.exists():
            _LOGGER.info("Cloning notes repo to %s", GITHUB_NOTES_DIR)
            subprocess.run(
                ["git", "clone", GITHUB_NOTES_REPO, str(GITHUB_NOTES_DIR)],
                capture_output=True, text=True, check=True,
            )
        else:
            _git(["pull", "--ff-only"], GITHUB_NOTES_DIR)
        return True
    except subprocess.CalledProcessError as e:
        _LOGGER.error("git error: %s\n%s", e, e.stderr)
        return False


def _build_lecture_files(lecture_id: int) -> list[tuple[Path, str]] | None:
    """Read DB and return list of (path, content) pairs to write. Returns None to skip."""
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            return None
        course = session.get(Course, lec.course_id)
        if not course:
            return None

        lecture_date = lec.date or "1970-01-01"
        year = lecture_date[:4]
        course_title = course.display_name or course.name

        notes_md = ""
        notes_model = None
        notes_date = None
        generated_title = None
        if lec.notes_status == "done":
            note = (
                session.query(Note)
                .filter(Note.lecture_id == lecture_id)
                .order_by(Note.id.desc())
                .first()
            )
            if note:
                notes_md = note.content_md or ""
                notes_model = note.model
                notes_date = _fmt_nz_date(note.created_at)
                generated_title = note.generated_title

        transcript_md = ""
        transcript_model = None
        transcript_date = None
        if lec.transcript_status == "done":
            transcript = (
                session.query(Transcript)
                .filter(Transcript.lecture_id == lecture_id)
                .order_by(Transcript.id.desc())
                .first()
            )
            if transcript:
                segments = json.loads(transcript.segments)
                transcript_md = _format_transcript_md(segments)
                transcript_model = transcript.model
                transcript_date = _fmt_nz_date(transcript.created_at)

    base_title = lec.title
    lecture_title = f"{lecture_date} - {base_title} - {generated_title}" if generated_title else f"{lecture_date} - {base_title}"

    info_footer = _generation_info(
        transcript_model=transcript_model, transcript_date=transcript_date,
        notes_model=notes_model, notes_date=notes_date,
    )

    notes_body = notes_md or ""
    if info_footer:
        notes_body = (notes_body.rstrip() + "\n\n" + info_footer) if notes_body else info_footer

    transcript_body = transcript_md or ""
    if transcript_body:
        transcript_info = _generation_info(transcript_model=transcript_model, transcript_date=transcript_date)
        if transcript_info:
            transcript_body = transcript_body.rstrip() + "\n\n" + transcript_info

    safe_dir = GITHUB_NOTES_DIR / _safe_filename(year) / _safe_filename(course_title)
    safe_lecture = _safe_filename(lecture_title)

    files = []
    if notes_body:
        files.append((safe_dir / f"{safe_lecture}.md", notes_body))
    if transcript_body:
        files.append((safe_dir / f"{safe_lecture}_transcript.md", transcript_body))
    return files


def sync_lecture_to_github(lecture_id: int) -> None:
    """Write lecture notes/transcript and push. Silently no-ops if GITHUB_NOTES_REPO unset."""
    if not GITHUB_NOTES_REPO:
        return
    try:
        _sync(lecture_id)
    except Exception:
        _LOGGER.exception("GitHub sync failed for lecture %d", lecture_id)


def _sync(lecture_id: int) -> None:
    files = _build_lecture_files(lecture_id)
    if not files:
        return

    with _GIT_LOCK:
        if not _ensure_repo():
            return
        _write_and_commit(files, message=f"sync: {files[0][0].stem}")


def bulk_sync_to_github(lecture_ids: list[int], workers: int = 16) -> None:
    """Write all lectures in parallel (DB reads), then single commit + push."""
    if not GITHUB_NOTES_REPO:
        return

    _LOGGER.info("Building file content for %d lectures (%d workers)...", len(lecture_ids), workers)
    all_files: list[tuple[Path, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_build_lecture_files, lid): lid for lid in lecture_ids}
        for i, fut in enumerate(as_completed(futures), 1):
            lid = futures[fut]
            try:
                result = fut.result()
                if result:
                    all_files.extend(result)
            except Exception:
                _LOGGER.exception("Failed to build files for lecture %d", lid)
            if i % 100 == 0:
                _LOGGER.info("  built %d/%d", i, len(lecture_ids))

    if not all_files:
        _LOGGER.info("No files to write.")
        return

    _LOGGER.info("Writing %d files and pushing...", len(all_files))
    with _GIT_LOCK:
        if not _ensure_repo():
            return
        _write_and_commit(all_files, message=f"bulk sync: {len(lecture_ids)} lectures")

    _LOGGER.info("Done — pushed %d files.", len(all_files))


def _write_and_commit(files: list[tuple[Path, str]], message: str) -> None:
    """Write files, stage, commit (if changed), and push."""
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    _git(["add", "-A"], GITHUB_NOTES_DIR)

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(GITHUB_NOTES_DIR),
    )
    if result.returncode == 0:
        _LOGGER.debug("No changes to commit.")
        return

    _git(["commit", "-m", message], GITHUB_NOTES_DIR)
    _git(["push"], GITHUB_NOTES_DIR)
