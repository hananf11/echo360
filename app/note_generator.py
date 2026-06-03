"""LLM-powered note generation from lecture transcripts via LiteLLM."""
import json
import logging
import os

import litellm

from app.database import get_db
from app.llm import router
from app.models import Course, Lecture, Note, Transcript
from app import jobs

_LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert lecture note-taker. You will be given a timestamped transcript of a lecture.

You MUST respond with a single JSON object matching this exact schema:

{
  "notes": "<structured markdown notes>",
  "title": "<short descriptive title for this lecture>",
  "action_items": [
    {"task": "<assignment, reading, deadline, or task mentioned>", "due_date": "<exact date or null>"},
    ...
  ]
}

Rules for "notes":
- Produce thorough, structured markdown lecture notes.
- You MUST follow this exact structure:

## <Topic Heading>

### <Subtopic>

* bullet points for content
* **bold** for key terms and definitions
* > blockquotes for notable quotes from the lecturer

## <Next Topic Heading>
...

## Key Terms

| Term | Definition |
|------|------------|
| **Term** | Definition |

- Content rules:
  - Cover all key topics, concepts, definitions, examples, formulas mentioned.
  - Use ## for major topic sections, ### for subtopics. Never use # (that's reserved for the document title).
  - Use tables for comparisons and structured information.
  - Be detailed but concise. Organise by topic, not chronologically.
  - The correct course code will be provided in the prompt. Use it when referencing the course — the transcript audio often garbles course codes (e.g. "sci 101" should be "SCIE101").
  - Always end with a "Key Terms" section (use "None mentioned" if truly empty).
  - Do NOT include an "Action Items" section in the notes markdown — action items go in the separate "action_items" JSON field instead.

Rules for "title":
- A short (3-8 word) descriptive title summarising the lecture's main topic.
- Do NOT include the course name, course code, lecture number, or date.
- Examples: "Introduction to MapReduce", "Scientific Reasoning and Citation", "Mātauranga Māori and Worldview"

Rules for "action_items":
- Extract any assignments, readings, deadlines, or tasks mentioned by the lecturer.
- Each item has "task" (description of what to do) and "due_date" (exact date string like "2024-03-15" or null if no date mentioned).
- Always use exact dates rather than relative references (e.g. "March 15" not "next week").
- Return an empty array if no action items are mentioned.

IMPORTANT: Your entire response must be valid JSON. No text before or after the JSON object. No markdown code fences."""


RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "lecture_notes",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Structured markdown lecture notes",
                },
                "title": {
                    "type": "string",
                    "description": "Short descriptive title for the lecture (3-8 words)",
                },
                "action_items": {
                    "type": "array",
                    "description": "Assignments, readings, deadlines, or tasks mentioned",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "Description of the action item",
                            },
                            "due_date": {
                                "type": ["string", "null"],
                                "description": "Due date as a string (e.g. 2024-03-15) or null",
                            },
                        },
                        "required": ["task", "due_date"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "notes", "action_items"],
            "additionalProperties": False,
        },
    },
}

# Simpler json_object mode for models that don't support json_schema
RESPONSE_FORMAT_JSON = {"type": "json_object"}


def _format_transcript(segments: list[dict]) -> str:
    """Format transcript segments as timestamped text for the LLM."""
    lines = []
    for seg in segments:
        mins = int(seg["start"] // 60)
        secs = int(seg["start"] % 60)
        lines.append(f"[{mins:02d}:{secs:02d}] {seg['text']}")
    return "\n".join(lines)


def _parse_response(raw: str) -> tuple[str, str, list[dict]]:
    """Parse JSON response into title, markdown notes, and action items."""
    # Strip markdown code fences if the model wrapped the JSON
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        _LOGGER.error("Failed to parse LLM JSON response (len=%d): %r", len(text), text[:500])
        raise

    title = data.get("title", "").strip()
    notes_md = data.get("notes", "").strip()

    # Validate action_items structure
    action_items = []
    for item in data.get("action_items", []):
        if isinstance(item, dict) and "task" in item:
            action_items.append({
                "task": str(item["task"]),
                "due_date": str(item["due_date"]) if item.get("due_date") else None,
            })

    return title, notes_md, action_items


def _is_schema_error(err: Exception) -> bool:
    """Check if an error is due to json_schema response_format not being supported."""
    err_str = str(err).lower()
    return "response_format" in err_str or "json_schema" in err_str or "schema" in err_str


async def _acompletion_with_schema_fallback(completion_fn, kwargs: dict) -> object:
    """Call completion with json_schema, fall back to json_object if unsupported."""
    kwargs["response_format"] = RESPONSE_SCHEMA
    try:
        return await completion_fn(**kwargs)
    except Exception as e:
        if _is_schema_error(e):
            _LOGGER.info("json_schema not supported, retrying with json_object")
            kwargs["response_format"] = RESPONSE_FORMAT_JSON
            return await completion_fn(**kwargs)
        raise


async def generate_notes(lecture_id: int, model: str) -> None:
    """Generate notes for a lecture from its transcript."""
    with get_db() as session:
        lec = session.get(Lecture, lecture_id)
        if not lec:
            _LOGGER.warning("generate_notes[%d]: lecture not found", lecture_id)
            return
        if lec.transcript_status != "done":
            _LOGGER.warning(
                "generate_notes[%d]: transcript not ready (status=%s)",
                lecture_id, lec.transcript_status,
            )
            return
        course_id = lec.course_id
        course = session.get(Course, course_id)
        course_name = (course.display_name or course.name) if course else "Unknown"

        # Get latest transcript
        transcript = (
            session.query(Transcript)
            .filter(Transcript.lecture_id == lecture_id)
            .order_by(Transcript.id.desc())
            .first()
        )
        if not transcript:
            _LOGGER.warning("generate_notes[%d]: no transcript row found", lecture_id)
            return
        segments = json.loads(transcript.segments)
        lecture_title = lec.title
        lecture_date = lec.date

    _LOGGER.info(
        "generate_notes[%d]: %r (%s) course=%r model=%s segments=%d",
        lecture_id, lecture_title, lecture_date, course_name, model, len(segments),
    )

    try:
        with get_db() as session:
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "generating"
                lec.error_message = None
        jobs.broadcast({"type": "notes_start", "lecture_id": lecture_id, "course_id": course_id})

        # Determine model: env override → specific model → auto (router fallback chain)
        env_model = os.environ.get("NOTES_LLM_MODEL")
        specific_model = env_model or (model if model != "auto" else None)

        formatted = _format_transcript(segments)
        prompt_chars = len(formatted)
        _LOGGER.info(
            "generate_notes[%d]: transcript formatted (%d chars, ~%d tokens)",
            lecture_id, prompt_chars, prompt_chars // 4,
        )
        user_msg = f"# Course: {course_name}\n# Lecture: {lecture_title}\n# Date: {lecture_date}\n\n{formatted}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        base_kwargs = {"messages": messages, "max_tokens": 8192, "temperature": 0.3}

        import time as _time
        t0 = _time.monotonic()
        if specific_model:
            # Direct call for a specific model (bypass router)
            _LOGGER.info("generate_notes[%d]: calling model=%s", lecture_id, specific_model)
            response = await _acompletion_with_schema_fallback(
                litellm.acompletion, {"model": specific_model, **base_kwargs},
            )
        else:
            # Auto mode: router handles fallback chain with cooldowns
            _LOGGER.info("generate_notes[%d]: using router auto-fallback", lecture_id)
            response = await _acompletion_with_schema_fallback(
                router.acompletion, {"model": "notes", **base_kwargs},
            )

        elapsed = _time.monotonic() - t0
        llm_model = response.model or specific_model or "unknown"
        content = response.choices[0].message.content
        _LOGGER.info(
            "generate_notes[%d]: LLM responded in %.1fs model=%s response_chars=%d",
            lecture_id, elapsed, llm_model, len(content or ""),
        )
        if not content or not content.strip():
            raise RuntimeError("Empty response from LLM")

        generated_title, notes_md, action_items = _parse_response(content)
        if not notes_md:
            raise RuntimeError("Parsed notes are empty")

        _LOGGER.info("Success with model: %s (title=%r, %d action items)", llm_model, generated_title, len(action_items))

        # Store in DB
        with get_db() as session:
            session.add(Note(
                lecture_id=lecture_id,
                model=llm_model,
                content_md=notes_md,
                generated_title=generated_title or None,
                action_items=json.dumps(action_items) if action_items else None,
            ))
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "done"
                lec.notes_model = llm_model

        jobs.broadcast({"type": "notes_done", "lecture_id": lecture_id, "course_id": course_id})

        from app.outline_sync import sync_lecture_to_outline
        sync_lecture_to_outline(lecture_id)

        from app.github_sync import sync_lecture_to_github
        sync_lecture_to_github(lecture_id)

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
