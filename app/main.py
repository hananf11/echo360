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
from sse_starlette.sse import EventSourceResponse

from app import db, jobs, scraper, transcriber

STATIC_DIR = Path(__file__).parent / "static"
AUDIO_DIR = os.environ.get("ECHO360_AUDIO_DIR", os.path.expanduser("~/echo360-library"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.set_loop(asyncio.get_event_loop())
    db.init_db()
    await jobs.start_workers()
    # Recover lectures stuck in 'downloaded' — re-enqueue conversion
    _recover_downloaded()
    yield
    jobs.shutdown()


def _recover_downloaded():
    """Re-enqueue conversion for lectures stuck in 'downloaded' after restart."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.raw_path, l.date, l.title, c.name AS course_name
            FROM lectures l JOIN courses c ON l.course_id = c.id
            WHERE l.audio_status = 'downloaded' AND l.raw_path IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        if row["raw_path"] and os.path.exists(row["raw_path"]):
            course_dir = os.path.join(
                AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", row["course_name"])
            )
            filename = re.sub(r'[\\/:*?"<>|]', "_", f"{row['date']} - {row['title']}")[:150]
            jobs.enqueue_convert(row["id"], row["raw_path"], course_dir, filename)


app = FastAPI(lifespan=lifespan)


# ── Request models ────────────────────────────────────────────────────────────

class AddCourseRequest(BaseModel):
    url: str


class TranscribeRequest(BaseModel):
    model: str = "tiny"


# ── Courses ───────────────────────────────────────────────────────────────────

@app.get("/api/courses")
def list_courses():
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, COUNT(l.id) AS lecture_count,
                   MIN(substr(l.date, 1, 4)) AS year,
                   SUM(CASE WHEN l.audio_status IN ('downloading', 'downloaded', 'converting') THEN 1 ELSE 0 END) AS downloading_count,
                   SUM(CASE WHEN l.audio_status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
                   SUM(COALESCE(l.duration_seconds, 0)) AS total_duration_seconds
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

    if row["audio_status"] not in ("pending", "error"):
        return {"status": row["audio_status"]}

    with db.get_db() as conn:
        conn.execute("UPDATE lectures SET audio_status = 'queued' WHERE id = ?", [lecture_id])
    jobs.broadcast({"type": "lecture_update", "lecture_id": lecture_id, "course_id": row["course_id"], "status": "queued"})

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", row["course_name"])
    )
    jobs.enqueue_download(lecture_id, course_dir)
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

    lecture_ids = [lec["id"] for lec in lectures]
    if lecture_ids:
        with db.get_db() as conn:
            conn.executemany(
                "UPDATE lectures SET audio_status = 'queued' WHERE id = ?",
                [(lid,) for lid in lecture_ids],
            )
        for lid in lecture_ids:
            jobs.broadcast({"type": "lecture_update", "lecture_id": lid, "course_id": course_id, "status": "queued"})

    course_dir = os.path.join(
        AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", course["name"])
    )
    for lec in lectures:
        jobs.enqueue_download(lec["id"], course_dir)
    return {"queued": len(lectures)}


# ── Transcription ─────────────────────────────────────────────────────────────

@app.get("/api/lectures/{lecture_id}/audio")
def stream_audio(lecture_id: int, request: Request):
    with db.get_db() as conn:
        row = conn.execute("SELECT audio_path FROM lectures WHERE id = ?", [lecture_id]).fetchone()
    if not row or not row["audio_path"]:
        raise HTTPException(404, "Audio not available")
    path = row["audio_path"]
    if not os.path.exists(path):
        raise HTTPException(404, "Audio file not found on disk")
    return FileResponse(path, media_type="audio/ogg", headers={"Accept-Ranges": "bytes"})


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
    jobs.submit_transcribe(transcriber.transcribe_lecture, lecture_id, model)
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
def transcribe_all(course_id: int, req: TranscribeRequest | None = None):
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

    model = req.model if req else "tiny"
    for lec in lectures:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE lectures SET transcript_status = 'queued' WHERE id = ?",
                [lec["id"]],
            )
        jobs.submit_transcribe(transcriber.transcribe_lecture, lec["id"], model)
    return {"queued": len(lectures)}


# ── Global download-all ──────────────────────────────────────────────────────

@app.post("/api/download-all")
def download_all_global():
    """Queue downloads for all pending/error lectures across every course."""
    with db.get_db() as conn:
        lectures = conn.execute(
            """
            SELECT l.id, l.course_id, c.name AS course_name
            FROM lectures l
            JOIN courses c ON l.course_id = c.id
            WHERE l.audio_status IN ('pending', 'error')
            """
        ).fetchall()

    if not lectures:
        return {"queued": 0}

    lecture_ids = [lec["id"] for lec in lectures]
    with db.get_db() as conn:
        conn.executemany(
            "UPDATE lectures SET audio_status = 'queued' WHERE id = ?",
            [(lid,) for lid in lecture_ids],
        )
    for lec in lectures:
        jobs.broadcast({"type": "lecture_update", "lecture_id": lec["id"], "course_id": lec["course_id"], "status": "queued"})

    for lec in lectures:
        course_dir = os.path.join(
            AUDIO_DIR, re.sub(r'[\\/:*?"<>|]', "_", lec["course_name"])
        )
        jobs.enqueue_download(lec["id"], course_dir)
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
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN audio_status = 'done' THEN 1 ELSE 0 END) AS downloaded FROM lectures"
        ).fetchone()
    return {
        "size_bytes": size_bytes,
        "total_lectures": row["total"],
        "downloaded_lectures": row["downloaded"],
    }


# ── Queue status ─────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue():
    """Return all lectures with an active audio or transcript status."""
    with db.get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.title, l.date, l.audio_status, l.transcript_status,
                   l.course_id, c.name AS course_name
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
            """
        ).fetchall()
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
