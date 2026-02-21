"""SQLAlchemy engine, session factory, and DB initialisation."""
import json
import logging
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

_LOGGER = logging.getLogger(__name__)

DB_PATH = os.environ.get("ECHO360_DB", "echo360.db")

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_db():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    _alembic_dir = Path(__file__).resolve().parent.parent / "alembic"
    _alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"

    insp = inspect(engine)
    has_alembic = insp.has_table("alembic_version")
    has_courses = insp.has_table("courses")

    if not has_alembic:
        if has_courses:
            # Existing DB without Alembic — stamp it at the initial migration
            _LOGGER.info("Existing database detected, stamping alembic at 0001")
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(_alembic_ini), "stamp", "0001"],
                check=True,
            )
        else:
            # Fresh DB — run all migrations
            _LOGGER.info("Fresh database, running alembic upgrade head")
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(_alembic_ini), "upgrade", "head"],
                check=True,
            )
    else:
        # Alembic already tracking — run pending migrations
        _LOGGER.info("Running pending alembic migrations")
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(_alembic_ini), "upgrade", "head"],
            check=True,
        )

    # Recovery logic (same as before)
    with get_db() as session:
        # Reset queued → pending on restart
        session.execute(
            text("UPDATE lectures SET audio_status = 'pending' WHERE audio_status = 'queued'")
        )
        _recover_downloading(session)
        _recover_converting(session)
        session.execute(
            text("UPDATE lectures SET transcript_status = 'pending' WHERE transcript_status IN ('queued', 'transcribing')")
        )
        session.execute(
            text("UPDATE lectures SET notes_status = 'pending' WHERE notes_status IN ('queued', 'generating')")
        )
        _backfill_durations(session)


def _recover_downloading(session: Session) -> None:
    from app.models import Lecture
    lectures = session.query(Lecture).filter(Lecture.audio_status == "downloading").all()
    for lec in lectures:
        if lec.raw_path and os.path.exists(lec.raw_path):
            lec.audio_status = "downloaded"
        else:
            lec.audio_status = "error"
            lec.error_message = "Download interrupted"


def _recover_converting(session: Session) -> None:
    from app.models import Lecture
    lectures = session.query(Lecture).filter(Lecture.audio_status == "converting").all()
    for lec in lectures:
        if lec.raw_path and os.path.exists(lec.raw_path):
            lec.audio_status = "downloaded"
        else:
            lec.audio_status = "pending"


def _backfill_durations(session: Session) -> None:
    from app.models import Lecture
    lectures = (
        session.query(Lecture)
        .filter(Lecture.duration_seconds.is_(None), Lecture.raw_json.isnot(None))
        .all()
    )
    for lec in lectures:
        try:
            v = json.loads(lec.raw_json)
            secs = _compute_duration_from_json(v)
            if secs:
                lec.duration_seconds = secs
        except Exception:
            pass


def _compute_duration_from_json(v: dict) -> int | None:
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
