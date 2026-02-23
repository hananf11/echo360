import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

# Configure root logger so app.* modules output to stderr (visible in docker logs)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  [%(name)s] %(message)s")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from app.database import get_db, init_db
from app.models import Course, Lecture, Note, Transcript
from app import jobs, scraper

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = os.environ.get("ECHO360_AUDIO_DIR", os.path.expanduser("~/echo360-library"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.set_loop(asyncio.get_event_loop())
    init_db()
    await jobs.start_workers()
    # Recover lectures stuck in 'downloaded' — re-enqueue conversion
    _recover_downloaded()
    # Remove leftover raw files from previous runs
    _cleanup_raw_files()
    yield
    jobs.shutdown()


def _recover_downloaded():
    """Re-enqueue conversion for lectures stuck in 'downloaded' after restart."""
    with get_db() as session:
        lectures = (
            session.query(Lecture)
            .join(Course, Lecture.course_id == Course.id)
            .filter(Lecture.audio_status == "downloaded", Lecture.raw_path.isnot(None))
            .all()
        )
        rows = [(lec.id, lec.raw_path, lec.date, lec.title, lec.course.name) for lec in lectures]
    for lid, raw_path, date, title, course_name in rows:
        if raw_path and os.path.exists(raw_path):
            course_dir = os.path.join(
                AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
            )
            filename = re.sub(r'[\\/:*?"<>|]', "_", f"{date} - {title}")[:150]
            jobs.enqueue_convert(lid, raw_path, course_dir, filename)


def _cleanup_raw_files():
    """Remove leftover raw files (.mp4, .m4s, .ts) where .opus conversion already exists."""
    import logging
    logger = logging.getLogger(__name__)
    removed, freed = 0, 0
    if not os.path.isdir(AUDIO_DIR):
        return
    for dirpath, _, filenames in os.walk(AUDIO_DIR):
        opus_stems = {os.path.splitext(f)[0] for f in filenames if f.endswith(".opus")}
        for f in filenames:
            stem, ext = os.path.splitext(f)
            if ext.lower() in (".mp4", ".m4s", ".ts"):
                # Check both exact stem and with _audio suffix stripped
                base = stem.removesuffix("_audio")
                if base in opus_stems or stem in opus_stems:
                    raw_path = os.path.join(dirpath, f)
                    try:
                        freed += os.path.getsize(raw_path)
                        os.remove(raw_path)
                        removed += 1
                    except OSError:
                        pass
    if removed:
        logger.info("Cleanup: removed %d leftover raw files, freed %.2f GB", removed, freed / (1024**3))
    # Clear stale raw_path references in DB for completed lectures
    with get_db() as session:
        session.query(Lecture).filter(
            Lecture.audio_status == "done", Lecture.raw_path.isnot(None)
        ).update({"raw_path": None}, synchronize_session=False)


app = FastAPI(lifespan=lifespan)


# ── Request models ────────────────────────────────────────────────────────────

class AddCourseRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    model: str = "tiny"


class GenerateNotesRequest(BaseModel):
    model: str = "auto"


class UpdateDisplayNameRequest(BaseModel):
    display_name: str | None = None


class BulkIdsRequest(BaseModel):
    lecture_ids: list[int]
    model: str | None = None


# ── Courses ───────────────────────────────────────────────────────────────────

@app.get("/api/courses")
def list_courses():
    with get_db() as session:
        rows = (
            session.query(
                Course,
                func.count(Lecture.id).label("lecture_count"),
                func.min(func.substr(Lecture.date, 1, 4)).label("year"),
                func.sum(case((Lecture.audio_status.in_(("downloading", "downloaded", "converting")), 1), else_=0)).label("downloading_count"),
                func.sum(case((Lecture.audio_status == "queued", 1), else_=0)).label("queued_count"),
                func.sum(case((Lecture.audio_status == "done", 1), else_=0)).label("downloaded_count"),
                func.sum(case((Lecture.audio_status == "no_media", 1), else_=0)).label("no_media_count"),
                func.sum(case((Lecture.transcript_status == "done", 1), else_=0)).label("transcribed_count"),
                func.sum(case((Lecture.notes_status == "done", 1), else_=0)).label("notes_count"),
                func.sum(func.coalesce(Lecture.duration_seconds, 0)).label("total_duration_seconds"),
            )
            .outerjoin(Lecture, Lecture.course_id == Course.id)
            .group_by(Course.id)
            .order_by(Course.name)
            .all()
        )
    return [
        {
            **course.to_dict(),
            "lecture_count": lecture_count,
            "year": year,
            "downloading_count": downloading_count,
            "queued_count": queued_count,
            "downloaded_count": downloaded_count,
            "no_media_count": no_media_count,
            "transcribed_count": transcribed_count,
            "notes_count": notes_count,
            "total_duration_seconds": total_duration_seconds,
        }
        for course, lecture_count, year, downloading_count, queued_count,
            downloaded_count, no_media_count, transcribed_count, notes_count,
            total_duration_seconds in rows
    ]


@app.post("/api/courses", status_code=201)
def add_course(req: AddCourseRequest):
    url = req.url.strip()
    section_id = scraper._extract_section_id(url)
    hostname = scraper._extract_hostname(url)

    if not section_id:
        raise HTTPException(400, "Could not find a section UUID in the URL")

    with get_db() as session:
        try:
            course = Course(url=url, section_id=section_id, hostname=hostname)
            session.add(course)
            session.flush()
            course_id = course.id
        except IntegrityError:
            raise HTTPException(409, "Course already added")

    jobs.submit(scraper.sync_course, course_id, url)

    with get_db() as session:
        course = session.get(Course, course_id)
        return course.to_dict()


@app.get("/api/courses/{course_id}")
def get_course(course_id: int):
    with get_db() as session:
        course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    return {**course.to_dict(), "syncing": jobs.is_syncing(course_id)}


@app.post("/api/courses/discover")
def discover_courses(req: AddCourseRequest):
    """Scrape the Echo360 /courses page and bulk-add every course found."""
    url = req.url.strip()
    course_urls = scraper.discover_course_urls(url)
    if not course_urls:
        raise HTTPException(404, "No courses found on that page")

    added, skipped = 0, 0
    for course_url in course_urls:
        section_id = scraper._extract_section_id(course_url)
        hostname = scraper._extract_hostname(course_url)
        try:
            with get_db() as session:
                course = Course(url=course_url, section_id=section_id, hostname=hostname)
                session.add(course)
                session.flush()
                course_id = course.id
            jobs.submit(scraper.sync_course, course_id, course_url)
            added += 1
        except IntegrityError:
            skipped += 1

    return {"added": added, "skipped": skipped}


@app.post("/api/courses/{course_id}/sync")
def sync_course(course_id: int):
    with get_db() as session:
        course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    jobs.submit(scraper.sync_course, course_id, course.url)
    return {"status": "syncing"}


@app.delete("/api/courses/{course_id}", status_code=204)
def delete_course(course_id: int):
    with get_db() as session:
        course = session.get(Course, course_id)
        if course:
            session.delete(course)


@app.patch("/api/courses/{course_id}")
def update_course(course_id: int, req: UpdateDisplayNameRequest):
    with get_db() as session:
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Course not found")
        course.display_name = req.display_name
    with get_db() as session:
        course = session.get(Course, course_id)
        return course.to_dict()


@app.post("/api/courses/{course_id}/fix-titles")
def fix_titles(course_id: int):
    with get_db() as session:
        course = session.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    jobs.enqueue_clean_titles(course_id)
    return {"status": "queued"}


# ── Lectures ──────────────────────────────────────────────────────────────────

@app.get("/api/courses/{course_id}/lectures")
def list_lectures(course_id: int):
    with get_db() as session:
        lectures = (
            session.query(Lecture)
            .filter(Lecture.course_id == course_id)
            .order_by(Lecture.date.desc())
            .all()
        )
    return [lec.to_dict() for lec in lectures]


@app.post("/api/lectures/{lecture_id}/download")
def download_lecture(lecture_id: int):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        row = lec.to_dict()
        course_name = lec.course.name

    if row["audio_status"] not in ("pending", "error", "no_media"):
        return {"status": row["audio_status"]}

    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.audio_status = "queued"
    jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": row["course_id"], "status": "queued"})

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
    )
    jobs.enqueue_download(lecture_id, course_dir)
    return {"status": "queued"}


@app.post("/api/courses/{course_id}/download-all")
def download_all(course_id: int):
    with get_db() as session:
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Course not found")
        course_name = course.name
        lectures = (
            session.query(Lecture)
            .filter(Lecture.course_id == course_id, Lecture.audio_status.in_(("pending", "error")))
            .all()
        )
        lecture_data = [lec.to_dict() for lec in lectures]

    lecture_ids = [lec["id"] for lec in lecture_data]
    if lecture_ids:
        with get_db() as session:
            session.query(Lecture).filter(Lecture.id.in_(lecture_ids)).update(
                {"audio_status": "queued"}, synchronize_session=False
            )
        for lid in lecture_ids:
            jobs.broadcast({"type": "lecture_update", "lecture_id": lid, "course_id": course_id, "status": "queued"})

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
    )
    for lec in lecture_data:
        jobs.enqueue_download(lec["id"], course_dir)
    return {"queued": len(lecture_data)}


# ── Re-download ───────────────────────────────────────────────────────────────

@app.post("/api/lectures/{lecture_id}/redownload")
def redownload_lecture(lecture_id: int):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        course_name = lec.course.name
        course_id = lec.course_id
        # Delete existing audio file
        if lec.audio_path and os.path.exists(lec.audio_path):
            os.remove(lec.audio_path)
        lec.audio_status = "queued"
        lec.audio_path = None
        lec.raw_path = None
        lec.transcript_status = "pending"
        lec.notes_status = "pending"
        lec.error_message = None

    jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": course_id, "status": "queued"})
    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
    )
    jobs.enqueue_download(lecture_id, course_dir)
    return {"status": "queued"}


@app.post("/api/lectures/bulk-redownload")
def bulk_redownload(req: BulkIdsRequest):
    if not req.lecture_ids:
        return {"queued": 0}

    rows = []
    with get_db() as session:
        for lid in req.lecture_ids:
            lec = session.get(Lecture, lid)
            if not lec:
                continue
            if lec.audio_path and os.path.exists(lec.audio_path):
                os.remove(lec.audio_path)
            lec.audio_status = "queued"
            lec.audio_path = None
            lec.raw_path = None
            lec.transcript_status = "pending"
            lec.notes_status = "pending"
            lec.error_message = None
            rows.append((lec.id, lec.course_id, lec.course.name))

    for lid, cid, course_name in rows:
        jobs.broadcast({"type": "lecture_update", "lecture_id": lid, "course_id": cid, "status": "queued"})
        course_dir = os.path.join(
            AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
        )
        jobs.enqueue_download(lid, course_dir)
    return {"queued": len(rows)}


@app.post("/api/lectures/bulk-download")
def bulk_download(req: BulkIdsRequest):
    if not req.lecture_ids:
        return {"queued": 0}

    rows = []
    with get_db() as session:
        for lid in req.lecture_ids:
            lec = session.get(Lecture, lid)
            if not lec or lec.audio_status not in ("pending", "error", "no_media"):
                continue
            lec.audio_status = "queued"
            rows.append((lec.id, lec.course_id, lec.course.name))

    for lid, cid, course_name in rows:
        jobs.broadcast({"type": "lecture_update", "lecture_id": lid, "course_id": cid, "status": "queued"})
        course_dir = os.path.join(
            AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name)
        )
        jobs.enqueue_download(lid, course_dir)
    return {"queued": len(rows)}


@app.post("/api/lectures/bulk-transcribe")
def bulk_transcribe(req: BulkIdsRequest):
    if not req.lecture_ids:
        return {"queued": 0}

    model = req.model or "modal"
    queued = []
    with get_db() as session:
        for lid in req.lecture_ids:
            lec = session.get(Lecture, lid)
            if not lec or lec.audio_status != "done":
                continue
            lec.transcript_status = "queued"
            queued.append(lid)

    for lid in queued:
        jobs.enqueue_transcribe(lid, model)
    return {"queued": len(queued)}


@app.post("/api/lectures/bulk-generate-notes")
def bulk_generate_notes(req: BulkIdsRequest):
    if not req.lecture_ids:
        return {"queued": 0}

    model = req.model or "openrouter/meta-llama/llama-3.3-70b-instruct"
    queued = []
    with get_db() as session:
        for lid in req.lecture_ids:
            lec = session.get(Lecture, lid)
            if not lec or lec.transcript_status != "done":
                continue
            lec.notes_status = "queued"
            queued.append(lid)

    for lid in queued:
        jobs.enqueue_generate_notes(lid, model)
    return {"queued": len(queued)}


# ── Transcription ─────────────────────────────────────────────────────────────

@app.get("/api/lectures/{lecture_id}/audio")
def stream_audio(lecture_id: int, request: Request):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
    if not lec or not lec.audio_path:
        raise HTTPException(404, "Audio not available")
    path = lec.audio_path
    if not os.path.exists(path):
        raise HTTPException(404, "Audio file not found on disk")
    return FileResponse(path, media_type="audio/ogg", headers={"Accept-Ranges": "bytes"})


@app.post("/api/lectures/{lecture_id}/transcribe")
def transcribe_lecture(lecture_id: int, req: TranscribeRequest | None = None):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        if lec.audio_status != "done":
            raise HTTPException(400, "Audio not downloaded yet")

    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.transcript_status = "queued"
    model = req.model if req else "groq"
    jobs.enqueue_transcribe(lecture_id, model)
    return {"status": "queued"}


@app.get("/api/lectures/{lecture_id}/transcript")
def get_transcript(lecture_id: int):
    with get_db() as session:
        transcript = (
            session.query(Transcript)
            .filter(Transcript.lecture_id == lecture_id)
            .order_by(Transcript.id.desc())
            .first()
        )
    if not transcript:
        raise HTTPException(404, "Transcript not found")
    return {
        "model": transcript.model,
        "segments": json.loads(transcript.segments),
        "created_at": transcript.created_at,
    }


@app.post("/api/courses/{course_id}/transcribe-all")
def transcribe_all(course_id: int, req: TranscribeRequest | None = None):
    with get_db() as session:
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Course not found")
        lectures = (
            session.query(Lecture)
            .filter(
                Lecture.course_id == course_id,
                Lecture.audio_status == "done",
                Lecture.transcript_status.in_(("pending", "error")),
            )
            .all()
        )
        lecture_ids = [lec.id for lec in lectures]

    model = req.model if req else "modal"
    for lid in lecture_ids:
        with get_db() as session:
            lec = session.get(Lecture, lid)
            if lec:
                lec.transcript_status = "queued"
        jobs.enqueue_transcribe(lid, model)
    return {"queued": len(lecture_ids)}


# ── Notes ──────────────────────────────────────────────────────────────────────

@app.post("/api/lectures/{lecture_id}/generate-notes")
def generate_notes(lecture_id: int, req: GenerateNotesRequest | None = None):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        if lec.transcript_status != "done":
            raise HTTPException(400, "Transcript not available yet")

    model = req.model if req else "openrouter/meta-llama/llama-3.3-70b-instruct"
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.notes_status = "queued"
    jobs.enqueue_generate_notes(lecture_id, model)
    return {"status": "queued"}


@app.get("/api/lectures/{lecture_id}/notes")
def get_notes(lecture_id: int):
    with get_db() as session:
        note = (
            session.query(Note)
            .filter(Note.lecture_id == lecture_id)
            .order_by(Note.id.desc())
            .first()
        )
    if not note:
        raise HTTPException(404, "Notes not found")
    return {
        "model": note.model,
        "content_md": note.content_md,
        "frame_timestamps": json.loads(note.frame_timestamps) if note.frame_timestamps else [],
        "created_at": note.created_at,
    }


@app.post("/api/courses/{course_id}/generate-notes-all")
def generate_notes_all(course_id: int, req: GenerateNotesRequest | None = None):
    with get_db() as session:
        course = session.get(Course, course_id)
        if not course:
            raise HTTPException(404, "Course not found")
        lectures = (
            session.query(Lecture)
            .filter(
                Lecture.course_id == course_id,
                Lecture.transcript_status == "done",
                Lecture.notes_status.in_(("pending", "error")),
            )
            .all()
        )
        lecture_ids = [lec.id for lec in lectures]

    model = req.model if req else "openrouter/meta-llama/llama-3.3-70b-instruct"
    for lid in lecture_ids:
        with get_db() as session:
            lec = session.get(Lecture, lid)
            if lec:
                lec.notes_status = "queued"
        jobs.enqueue_generate_notes(lid, model)
    return {"queued": len(lecture_ids)}


# ── Frames ─────────────────────────────────────────────────────────────────────

@app.post("/api/lectures/{lecture_id}/extract-frames")
def extract_frames(lecture_id: int):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        if lec.notes_status != "done":
            raise HTTPException(400, "Notes not generated yet")

    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if lec:
            lec.frames_status = "queued"
    jobs.enqueue_extract_frames(lecture_id)
    return {"status": "queued"}


@app.get("/api/lectures/{lecture_id}/frames")
def get_frames(lecture_id: int):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        course_name = lec.course.name
        row = lec.to_dict()

        note = (
            session.query(Note)
            .filter(Note.lecture_id == lecture_id)
            .order_by(Note.id.desc())
            .first()
        )
        frame_timestamps = json.loads(note.frame_timestamps) if note and note.frame_timestamps else []

    if not frame_timestamps:
        return []

    course_dir = os.path.join(AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name))
    frames_dir = os.path.join(course_dir, "frames")
    filename_base = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")[:150]

    frames = []
    for ft in frame_timestamps:
        ts = int(ft["time"])
        frame_file = f"{filename_base}_{ts}s.jpg"
        frame_path = os.path.join(frames_dir, frame_file)
        if os.path.exists(frame_path):
            frames.append({
                "url": f"/api/lectures/{lecture_id}/frames/{ts}",
                "time": ft["time"],
                "reason": ft["reason"],
            })
    return frames


@app.get("/api/lectures/{lecture_id}/frames/{timestamp}")
def get_frame_image(lecture_id: int, timestamp: int):
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            raise HTTPException(404, "Lecture not found")
        course_name = lec.course.name
        row = lec.to_dict()

    course_dir = os.path.join(AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course_name))
    frames_dir = os.path.join(course_dir, "frames")
    filename_base = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")[:150]
    frame_path = os.path.join(frames_dir, f"{filename_base}_{timestamp}s.jpg")

    if not os.path.exists(frame_path):
        raise HTTPException(404, "Frame not found")
    return FileResponse(frame_path, media_type="image/jpeg")


# ── Global transcribe-all ────────────────────────────────────────────────────

@app.post("/api/transcribe-all")
def transcribe_all_global(req: TranscribeRequest | None = None):
    """Queue transcription for all done lectures with pending/error transcript across all courses."""
    with get_db() as session:
        lectures = (
            session.query(Lecture.id)
            .filter(
                Lecture.audio_status == "done",
                Lecture.transcript_status.in_(("pending", "error")),
            )
            .all()
        )

    if not lectures:
        return {"queued": 0}

    lecture_ids = [lec.id for lec in lectures]
    model = req.model if req else "modal"
    with get_db() as session:
        session.query(Lecture).filter(Lecture.id.in_(lecture_ids)).update(
            {"transcript_status": "queued"}, synchronize_session=False
        )
    for lid in lecture_ids:
        jobs.enqueue_transcribe(lid, model)
    return {"queued": len(lecture_ids)}


# ── Global download-all ──────────────────────────────────────────────────────

@app.post("/api/download-all")
def download_all_global():
    """Queue downloads for all pending/error lectures across every course."""
    with get_db() as session:
        lectures = (
            session.query(Lecture.id, Lecture.course_id, Course.name.label("course_name"))
            .join(Course, Lecture.course_id == Course.id)
            .filter(Lecture.audio_status.in_(("pending", "error")))
            .all()
        )

    if not lectures:
        return {"queued": 0}

    lecture_ids = [lec.id for lec in lectures]
    with get_db() as session:
        session.query(Lecture).filter(Lecture.id.in_(lecture_ids)).update(
            {"audio_status": "queued"}, synchronize_session=False
        )
    for lec in lectures:
        jobs.broadcast({"type": "lecture_update", "lecture_id": lec.id, "course_id": lec.course_id, "status": "queued"})

    for lec in lectures:
        course_dir = os.path.join(
            AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", lec.course_name)
        )
        jobs.enqueue_download(lec.id, course_dir)
    return {"queued": len(lectures)}


# ── Storage stats ────────────────────────────────────────────────────────────

def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


@app.get("/api/storage")
def get_storage():
    size_bytes = _dir_size(AUDIO_DIR) if os.path.isdir(AUDIO_DIR) else 0
    with get_db() as session:
        total = session.query(func.count(Lecture.id)).scalar()
        downloaded = session.query(func.count(Lecture.id)).filter(Lecture.audio_status == "done").scalar()
    return {
        "size_bytes": size_bytes,
        "total_lectures": total,
        "downloaded_lectures": downloaded,
    }


# ── Queue status ─────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue():
    """Return all lectures with an active audio or transcript status."""
    audio_order = case(
        (Lecture.audio_status == "downloading", 0),
        (Lecture.audio_status == "converting", 1),
        (Lecture.audio_status == "downloaded", 2),
        (Lecture.audio_status == "queued", 3),
        (Lecture.audio_status == "error", 4),
        else_=5,
    )
    with get_db() as session:
        rows = (
            session.query(
                Lecture.id, Lecture.title, Lecture.date,
                Lecture.audio_status, Lecture.transcript_status, Lecture.notes_status,
                Lecture.course_id, Course.name.label("course_name"), Lecture.error_message,
            )
            .join(Course, Lecture.course_id == Course.id)
            .filter(
                (Lecture.audio_status.notin_(("pending", "done", "no_media")))
                | (Lecture.transcript_status.notin_(("pending", "done")))
                | (Lecture.notes_status.notin_(("pending", "done")))
            )
            .order_by(audio_order, Lecture.id)
            .all()
        )
    return [row._asdict() for row in rows]


# ── SSE ───────────────────────────────────────────────────────────────────────

@app.get("/api/sse")
async def sse_endpoint():
    async def generator():
        async for msg in jobs.listen():
            yield {"data": msg}

    return EventSourceResponse(generator())


# ── Serve React SPA ───────────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        return FileResponse(STATIC_DIR / "index.html")
