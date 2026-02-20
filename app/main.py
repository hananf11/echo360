import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import db, jobs, scraper, transcriber

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = os.environ.get("ECHO360_AUDIO_DIR", os.path.expanduser("~/echo360-library"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.set_loop(asyncio.get_event_loop())
    db.init_db()
    yield
    jobs.shutdown()


app = FastAPI(lifespan=lifespan)


# ── Request models ────────────────────────────────────────────────────────────

class AddCourseRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    model: str = "turbo"


# ── Courses ───────────────────────────────────────────────────────────────────

@app.get("/api/courses")
def list_courses():
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, COUNT(l.id) AS lecture_count,
                   MIN(substr(l.date, 1, 4)) AS year,
                   SUM(CASE WHEN l.audio_status = 'downloading' THEN 1 ELSE 0 END) AS downloading_count,
                   SUM(CASE WHEN l.audio_status IN ('pending', 'error') THEN 1 ELSE 0 END) AS pending_count
            FROM   courses  c
            LEFT JOIN lectures l ON l.course_id = c.id
            GROUP BY c.id
            ORDER BY c.name
            """
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/courses", status_code=201)
def add_course(req: AddCourseRequest):
    url = req.url.strip()
    section_id = scraper._extract_section_id(url)
    hostname = scraper._extract_hostname(url)

    if not section_id:
        raise HTTPException(400, "Could not find a section UUID in the URL")

    with db.get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO courses (url, section_id, hostname) VALUES (?, ?, ?)",
                [url, section_id, hostname],
            )
            course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception:
            raise HTTPException(409, "Course already added")

    jobs.submit(scraper.sync_course, course_id, url)

    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", [course_id]).fetchone()
    return dict(row)


@app.get("/api/courses/{course_id}")
def get_course(course_id: int):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", [course_id]).fetchone()
    if not row:
        raise HTTPException(404, "Course not found")
    return dict(row)


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
            with db.get_db() as conn:
                conn.execute(
                    "INSERT INTO courses (url, section_id, hostname) VALUES (?, ?, ?)",
                    [course_url, section_id, hostname],
                )
                course_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            jobs.submit(scraper.sync_course, course_id, course_url)
            added += 1
        except Exception:
            skipped += 1

    return {"added": added, "skipped": skipped}


@app.post("/api/courses/{course_id}/sync")
def sync_course(course_id: int):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", [course_id]).fetchone()
    if not row:
        raise HTTPException(404, "Course not found")
    jobs.submit(scraper.sync_course, course_id, row["url"])
    return {"status": "syncing"}


@app.delete("/api/courses/{course_id}", status_code=204)
def delete_course(course_id: int):
    with db.get_db() as conn:
        conn.execute("DELETE FROM courses WHERE id = ?", [course_id])


# ── Lectures ──────────────────────────────────────────────────────────────────

@app.get("/api/courses/{course_id}/lectures")
def list_lectures(course_id: int):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? ORDER BY date DESC",
            [course_id],
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/lectures/{lecture_id}/download")
def download_lecture(lecture_id: int):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT l.*, c.name AS course_name FROM lectures l JOIN courses c ON l.course_id = c.id WHERE l.id = ?",
            [lecture_id],
        ).fetchone()
    if not row:
        raise HTTPException(404, "Lecture not found")

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", row["course_name"])
    )
    jobs.submit(scraper.download_lecture, lecture_id, course_dir)
    return {"status": "queued"}


@app.post("/api/courses/{course_id}/download-all")
def download_all(course_id: int):
    with db.get_db() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ?", [course_id]).fetchone()
        lectures = conn.execute(
            "SELECT * FROM lectures WHERE course_id = ? AND audio_status IN ('pending', 'error')",
            [course_id],
        ).fetchall()
    if not course:
        raise HTTPException(404, "Course not found")

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course["name"])
    )
    for lec in lectures:
        jobs.submit(scraper.download_lecture, lec["id"], course_dir)
    return {"queued": len(lectures)}


# ── Transcription ─────────────────────────────────────────────────────────────

@app.post("/api/lectures/{lecture_id}/transcribe")
def transcribe_lecture(lecture_id: int, req: TranscribeRequest | None = None):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM lectures WHERE id = ?", [lecture_id]).fetchone()
    if not row:
        raise HTTPException(404, "Lecture not found")
    if row["audio_status"] != "done":
        raise HTTPException(400, "Audio not downloaded yet")

    with db.get_db() as conn:
        conn.execute(
            "UPDATE lectures SET transcript_status = 'queued' WHERE id = ?",
            [lecture_id],
        )
    model = req.model if req else "turbo"
    jobs.submit(transcriber.transcribe_lecture, lecture_id, model)
    return {"status": "queued"}


@app.get("/api/lectures/{lecture_id}/transcript")
def get_transcript(lecture_id: int):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE lecture_id = ? ORDER BY id DESC LIMIT 1",
            [lecture_id],
        ).fetchone()
    if not row:
        raise HTTPException(404, "Transcript not found")
    return {
        "model": row["model"],
        "segments": json.loads(row["segments"]),
        "created_at": row["created_at"],
    }


@app.post("/api/courses/{course_id}/transcribe-all")
def transcribe_all(course_id: int):
    with db.get_db() as conn:
        course = conn.execute("SELECT * FROM courses WHERE id = ?", [course_id]).fetchone()
        lectures = conn.execute(
            """SELECT * FROM lectures WHERE course_id = ?
               AND audio_status = 'done'
               AND transcript_status IN ('pending', 'error')""",
            [course_id],
        ).fetchall()
    if not course:
        raise HTTPException(404, "Course not found")

    for lec in lectures:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'queued' WHERE id = ?",
                [lec["id"]],
            )
        jobs.submit(transcriber.transcribe_lecture, lec["id"])
    return {"queued": len(lectures)}


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
