"""LLM-powered note generation from lecture transcripts via LiteLLM."""
import json
import logging
import os
import re

import litellm

from app.database import get_db
from app.models import Lecture, Note, Transcript
from app import jobs

_LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct"

SYSTEM_PROMPT = """\
You are an expert lecture note-taker. Given a timestamped transcript of a lecture, produce two sections:

## Notes
Structured, detailed markdown notes covering:
- Key topics and themes
- Important concepts and definitions
- Significant examples or case studies
- Formulas, equations, or technical details mentioned
- Action items or assignments mentioned

Use headings, bullet points, and bold for emphasis. Be thorough but concise.

## Frame Timestamps
A JSON code block containing an array of objects, each with:
- `time`: the timestamp in seconds (number) where a visual aid, diagram, slide change, or important visual content is being discussed
- `reason`: a brief description of what visual content is likely shown

Only include timestamps where the speaker clearly references visual content (e.g., "as you can see on this slide", "looking at this diagram", "this graph shows").
If no visual references are found, return an empty array.

Example format:
```json
[
  {"time": 125.0, "reason": "Diagram of neural network architecture"},
  {"time": 340.5, "reason": "Graph showing training loss over epochs"}
]
```
"""


def _format_transcript(segments: list[dict]) -> str:
    """Format transcript segments as timestamped text for the LLM."""
    lines = []
    for seg in segments:
        mins = int(seg["start"] // 60)
        secs = int(seg["start"] % 60)
        lines.append(f"[{mins:02d}:{secs:02d}] {seg['text']}")
    return "\n".join(lines)


def _parse_response(text: str) -> tuple[str, list[dict]]:
    """Parse LLM response into markdown notes and frame timestamps."""
    # Split on ## Frame Timestamps header
    parts = re.split(r"^## Frame Timestamps\s*$", text, maxsplit=1, flags=re.MULTILINE)

    notes_md = parts[0].strip()
    # Remove leading ## Notes header if present
    notes_md = re.sub(r"^## Notes\s*\n", "", notes_md, count=1).strip()

    frame_timestamps = []
    if len(parts) > 1:
        # Extract JSON from code block
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", parts[1], re.DOTALL)
        if json_match:
            try:
                frame_timestamps = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                _LOGGER.warning("Failed to parse frame timestamps JSON")

    return notes_md, frame_timestamps


async def generate_notes(lecture_id: int, model: str) -> None:
    """Generate notes for a lecture from its transcript."""
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            return
        if lec.transcript_status != "done":
            return
        course_id = lec.course_id

        # Get latest transcript
        transcript = (
            session.query(Transcript)
            .filter(Transcript.lecture_id == lecture_id)
            .order_by(Transcript.id.desc())
            .first()
        )
        if not transcript:
            return
        segments = json.loads(transcript.segments)
        lecture_title = lec.title

    try:
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "generating"
                lec.error_message = None
        jobs.broadcast({"type": "notes_start", "lecture_id": lecture_id, "course_id": course_id})

        # Format transcript
        formatted = _format_transcript(segments)
        user_msg = f"# Lecture: {lecture_title}\n\n{formatted}"

        # Resolve model: env var override or passed model string
        llm_model = os.environ.get("NOTES_LLM_MODEL", model)

        # Call LiteLLM
        response = await litellm.acompletion(
            model=llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4096,
            temperature=0.3,
        )

        content = response.choices[0].message.content
        notes_md, frame_timestamps = _parse_response(content)

        if not notes_md:
            raise RuntimeError("LLM returned empty notes")

        # Store in DB
        with get_db() as session:
            session.add(Note(
                lecture_id=lecture_id,
                model=llm_model,
                content_md=notes_md,
                frame_timestamps=json.dumps(frame_timestamps) if frame_timestamps else None,
            ))
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "done"
                lec.notes_model = llm_model

        jobs.broadcast({"type": "notes_done", "lecture_id": lecture_id, "course_id": course_id})

    except Exception as e:
        _LOGGER.exception("Note generation failed for lecture %d", lecture_id)
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "error"
                lec.error_message = str(e)[:500]
        jobs.broadcast(
            {"type": "notes_error", "lecture_id": lecture_id, "course_id": course_id, "error": str(e)}
        )
