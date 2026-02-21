import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from app.database import get_db, init_db
from app.models import Course, Lecture, Transcript
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
        rows = session.execute(
            text("""
                SELECT l.id, l.raw_path, l.date, l.title, c.name AS course_name
                FROM lectures l JOIN courses c ON l.course_id = c.id
                WHERE l.audio_status = 'downloaded' AND l.raw_path IS NOT NULL
            """)
        ).mappings().all()
    for row in rows:
        if row["raw_path"] and os.path.exists(row["raw_path"]):
            course_dir = os.path.join(
                AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", row["course_name"])
            )
            filename = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")[:150]
            jobs.enqueue_convert(row["id"], row["raw_path"], course_dir, filename)


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
        session.execute(text("UPDATE lectures SET raw_path = NULL WHERE audio_status = 'done' AND raw_path IS NOT NULL"))


app = FastAPI(lifespan=lifespan)


# ── Request models ────────────────────────────────────────────────────────────

class AddCourseRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    model: str = "tiny"


# ── Courses ───────────────────────────────────────────────────────────────────

@app.get("/api/courses")
def list_courses():
    with get_db() as session:
        rows = session.execute(
            text("""
                SELECT c.*, COUNT(l.id) AS lecture_count,
                       MIN(substr(l.date, 1, 4)) AS year,
                       SUM(CASE WHEN l.audio_status IN ('downloading', 'downloaded', 'converting') THEN 1 ELSE 0 END) AS downloading_count,
                       SUM(CASE WHEN l.audio_status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
                       SUM(CASE WHEN l.audio_status = 'done' THEN 1 ELSE 0 END) AS downloaded_count,
                       SUM(CASE WHEN l.transcript_status = 'done' THEN 1 ELSE 0 END) AS transcribed_count,
                       SUM(COALESCE(l.duration_seconds, 0)) AS total_duration_seconds
                FROM   courses  c
                LEFT JOIN lectures l ON l.course_id = c.id
                GROUP BY c.id
                ORDER BY c.name
            """)
        ).mappings().all()
    return [dict(r) for r in rows]


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
    return course.to_dict()


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

    if row["audio_status"] not in ("pending", "error"):
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
        row = session.execute(
            text("SELECT COUNT(*) AS total, SUM(CASE WHEN audio_status = 'done' THEN 1 ELSE 0 END) AS downloaded FROM lectures")
        ).mappings().one()
    return {
        "size_bytes": size_bytes,
        "total_lectures": row["total"],
        "downloaded_lectures": row["downloaded"],
    }


# ── Queue status ─────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue():
    """Return all lectures with an active audio or transcript status."""
    with get_db() as session:
        rows = session.execute(
            text("""
                SELECT l.id, l.title, l.date, l.audio_status, l.transcript_status,
                       l.course_id, c.name AS course_name, l.error_message
                FROM lectures l
                JOIN courses c ON l.course_id = c.id
                WHERE l.audio_status NOT IN ('pending', 'done')
                   OR l.transcript_status NOT IN ('pending', 'done')
                ORDER BY
                    CASE l.audio_status
                        WHEN 'downloading' THEN 0
                        WHEN 'converting'  THEN 1
                        WHEN 'downloaded'  THEN 2
                        WHEN 'queued'      THEN 3
                        WHEN 'error'       THEN 4
                        ELSE 5
                    END,
                    l.id
            """)
        ).mappings().all()
    return [dict(r) for r in rows]


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
