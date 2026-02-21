import json
import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("ECHO360_DB", "echo360.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL DEFAULT 'Untitled',
    url           TEXT    NOT NULL UNIQUE,
    section_id    TEXT    NOT NULL,
    hostname      TEXT    NOT NULL,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS lectures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    echo_id       TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    date          TEXT    NOT NULL DEFAULT '1970-01-01',
    audio_path    TEXT,
    audio_status  TEXT    NOT NULL DEFAULT 'pending',
    raw_json      TEXT,
    UNIQUE(course_id, echo_id)
);

CREATE TABLE IF NOT EXISTS transcripts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id  INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    model       TEXT    NOT NULL,
    segments    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id    INTEGER REFERENCES lectures(id) ON DELETE SET NULL,
    course_id     INTEGER REFERENCES courses(id)  ON DELETE SET NULL,
    type          TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    progress      REAL    DEFAULT 0.0,
    error         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        for col, defn in [
            ("transcript_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("transcript_model", "TEXT"),
            ("duration_seconds", "INTEGER"),
            ("raw_path", "TEXT"),
            ("error_message", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE lectures ADD COLUMN {col} {defn}")
            except Exception:
                pass  # already exists
        # Reset any in-progress/queued states left over from a previous crash/restart
        conn.execute(
            "UPDATE lectures SET audio_status = 'pending' WHERE audio_status = 'queued'"
        )
        _recover_downloading(conn)
        # Converting lectures that still have a raw file on disk → downloaded (retry conversion)
        _recover_converting(conn)
        conn.execute(
            "UPDATE lectures SET transcript_status = 'pending' WHERE transcript_status IN ('queued', 'transcribing')"
        )
        # Backfill duration_seconds from raw_json for existing records
        _backfill_durations(conn)


def _recover_downloading(conn) -> None:
    """Lectures stuck in 'downloading' — if raw file exists, move to 'downloaded'."""
    rows = conn.execute(
        "SELECT id, raw_path FROM lectures WHERE audio_status = 'downloading'"
    ).fetchall()
    for row in rows:
        if row["raw_path"] and os.path.exists(row["raw_path"]):
            conn.execute("UPDATE lectures SET audio_status = 'downloaded' WHERE id = ?", [row["id"]])
        else:
            conn.execute("UPDATE lectures SET audio_status = 'pending' WHERE id = ?", [row["id"]])


def _recover_converting(conn) -> None:
    """Lectures stuck in 'converting' — if raw file exists, move to 'downloaded' for retry."""
    rows = conn.execute(
        "SELECT id, raw_path FROM lectures WHERE audio_status = 'converting'"
    ).fetchall()
    for row in rows:
        if row["raw_path"] and os.path.exists(row["raw_path"]):
            conn.execute("UPDATE lectures SET audio_status = 'downloaded' WHERE id = ?", [row["id"]])
        else:
            conn.execute("UPDATE lectures SET audio_status = 'pending' WHERE id = ?", [row["id"]])


def _backfill_durations(conn) -> None:
    """Compute duration_seconds from raw_json for lectures that don't have it yet."""
    rows = conn.execute(
        "SELECT id, raw_json FROM lectures WHERE duration_seconds IS NULL AND raw_json IS NOT NULL"
    ).fetchall()
    for row in rows:
        try:
            v = json.loads(row["raw_json"])
            secs = _compute_duration_from_json(v)
            if secs:
                conn.execute("UPDATE lectures SET duration_seconds = ? WHERE id = ?", [secs, row["id"]])
        except Exception:
            pass


def _compute_duration_from_json(v: dict) -> int | None:
    """Compute lecture duration in seconds from raw lesson JSON."""
    try:
        start = v["lesson"].get("startTimeUTC")
        end = v["lesson"].get("endTimeUTC")
        if start and end:
            dt_start = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S.%fZ")
            dt_end = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S.%fZ")
            secs = int((dt_end - dt_start).total_seconds())
            if secs > 0:
                return secs
    except (ValueError, TypeError, KeyError):
        pass
    try:
        timing = v["lesson"]["lesson"].get("timing", {})
        if timing.get("start") and timing.get("end"):
            dt_start = datetime.strptime(timing["start"], "%Y-%m-%dT%H:%M:%S.%f")
            dt_end = datetime.strptime(timing["end"], "%Y-%m-%dT%H:%M:%S.%f")
            secs = int((dt_end - dt_start).total_seconds())
            if secs > 0:
                return secs
    except (ValueError, TypeError, KeyError):
        pass
    return None


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
