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
  "frame_timestamps": [
    {"time": <seconds as number>, "reason": "<what visual is likely shown>"},
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

## Action Items

* List any assignments, readings, deadlines, or tasks mentioned

- Content rules:
  - Cover all key topics, concepts, definitions, examples, formulas mentioned.
  - Use ## for major topic sections, ### for subtopics. Never use # (that's reserved for the document title).
  - Use tables for comparisons and structured information.
  - Be detailed but concise. Organise by topic, not chronologically.
  - The correct course code will be provided in the prompt. Use it when referencing the course — the transcript audio often garbles course codes (e.g. "sci 101" should be "SCIE101").
  - Always end with "Key Terms" and "Action Items" sections (use "None mentioned" if truly empty).

Rules for "title":
- A short (3-8 word) descriptive title summarising the lecture's main topic.
- Do NOT include the course name, course code, lecture number, or date.
- Examples: "Introduction to MapReduce", "Scientific Reasoning and Citation", "Mātauranga Māori and Worldview"

Rules for "frame_timestamps":
- Identify moments where visual content is likely being shown or changed. Include timestamps for:
  1. Explicit visual references ("as you can see", "on this slide", "this diagram shows")
  2. Topic transitions where a new slide is likely shown (new subject introduced, "let's move on to", "next we have")
  3. When formulas, equations, or code are being explained in detail (likely written on screen)
  4. When examples with specific data, tables, or figures are discussed
  5. When the speaker describes spatial/visual concepts (graphs, architectures, flowcharts)
- Each entry needs "time" (seconds from start, as a number) and "reason" (brief description of likely visual content)
- Aim for at least 5-15 timestamps for a typical lecture. More for visually heavy lectures.
- If the lecture is purely conversational with zero visual references, return an empty array, but this should be rare.

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
                "frame_timestamps": {
                    "type": "array",
                    "description": "Timestamps where visual content is likely shown",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {
                                "type": "number",
                                "description": "Timestamp in seconds from start",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Brief description of likely visual content",
                            },
                        },
                        "required": ["time", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "notes", "frame_timestamps"],
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
    """Parse JSON response into title, markdown notes, and frame timestamps."""
    # Strip markdown code fences if the model wrapped the JSON
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    data = json.loads(text)

    title = data.get("title", "").strip()
    notes_md = data.get("notes", "").strip()
    frame_timestamps = data.get("frame_timestamps", [])

    # Validate frame_timestamps structure
    validated = []
    for ft in frame_timestamps:
        if isinstance(ft, dict) and "time" in ft and "reason" in ft:
            try:
                validated.append({
                    "time": float(ft["time"]),
                    "reason": str(ft["reason"]),
                })
            except (ValueError, TypeError):
                continue
    # Sort by time
    validated.sort(key=lambda x: x["time"])

    return title, notes_md, validated


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
            return
        if lec.transcript_status != "done":
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
        user_msg = f"# Course: {course_name}\n# Lecture: {lecture_title}\n\n{formatted}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        # Determine model: env override → specific model → auto (router fallback chain)
        env_model = os.environ.get("NOTES_LLM_MODEL")
        specific_model = env_model or (model if model != "auto" else None)

        base_kwargs = {"messages": messages, "max_tokens": 4096, "temperature": 0.3}

        if specific_model:
            # Direct call for a specific model (bypass router)
            _LOGGER.info("Using specific LLM model: %s", specific_model)
            response = await _acompletion_with_schema_fallback(
                litellm.acompletion, {"model": specific_model, **base_kwargs},
            )
        else:
            # Auto mode: router handles fallback chain with cooldowns
            _LOGGER.info("Using router auto-fallback for notes")
            response = await _acompletion_with_schema_fallback(
                router.acompletion, {"model": "notes", **base_kwargs},
            )

        llm_model = response.model or specific_model or "unknown"
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise RuntimeError("Empty response from LLM")

        generated_title, notes_md, frame_timestamps = _parse_response(content)
        if not notes_md:
            raise RuntimeError("Parsed notes are empty")

        _LOGGER.info("Success with model: %s (title=%r, %d frame timestamps)", llm_model, generated_title, len(frame_timestamps))

        # Store in DB
        with get_db() as session:
            session.add(Note(
                lecture_id=lecture_id,
                model=llm_model,
                content_md=notes_md,
                generated_title=generated_title or None,
                frame_timestamps=json.dumps(frame_timestamps) if frame_timestamps else None,
            ))
            lec = session.get(Lecture, lecture_id)
            if lec:
                lec.notes_status = "done"
                lec.notes_model = llm_model

        jobs.broadcast({"type": "notes_done", "lecture_id": lecture_id, "course_id": course_id})

        from app.outline_sync import sync_lecture_to_outline
        sync_lecture_to_outline(lecture_id)

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
