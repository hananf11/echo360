import sqlite3
import os
from contextlib import contextmanager

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
        ]:
            try:
                conn.execute(f"ALTER TABLE lectures ADD COLUMN {col} {defn}")
            except Exception:
                pass  # already exists


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
