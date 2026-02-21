"""SQLAlchemy ORM models for the echo360 web app."""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    def to_dict(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class Course(Base):
    __tablename__ = "courses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, default="Untitled")
    url = Column(String, nullable=False, unique=True)
    section_id = Column(String, nullable=False)
    hostname = Column(String, nullable=False)
    last_synced_at = Column(String)

    lectures = relationship("Lecture", back_populates="course", cascade="all, delete-orphan")


class Lecture(Base):
    __tablename__ = "lectures"
    __table_args__ = (UniqueConstraint("course_id", "echo_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    echo_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    date = Column(String, nullable=False, default="1970-01-01")
    audio_path = Column(String)
    audio_status = Column(String, nullable=False, default="pending")
    raw_json = Column(Text)
    transcript_status = Column(String, nullable=False, default="pending")
    transcript_model = Column(String)
    duration_seconds = Column(Integer)
    raw_path = Column(String)
    error_message = Column(String)

    course = relationship("Course", back_populates="lectures")
    transcripts = relationship("Transcript", back_populates="lecture", cascade="all, delete-orphan")


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lecture_id = Column(Integer, ForeignKey("lectures.id", ondelete="CASCADE"), nullable=False)
    model = Column(String, nullable=False)
    segments = Column(Text, nullable=False)
    created_at = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    lecture = relationship("Lecture", back_populates="transcripts")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lecture_id = Column(Integer, ForeignKey("lectures.id", ondelete="SET NULL"))
    course_id = Column(Integer, ForeignKey("courses.id", ondelete="SET NULL"))
    type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    progress = Column(Float, default=0.0)
    error = Column(Text)
    created_at = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    updated_at = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
