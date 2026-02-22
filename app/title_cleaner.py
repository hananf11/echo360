"""LLM-powered title cleanup for courses and lectures."""
import json
import logging

from app.database import get_db
from app.llm import router
from app.models import Course, Lecture
from app import jobs

_LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You clean up Echo360 course and lecture titles. You will receive a JSON object with the raw course name and a list of lectures (id, raw title, date).

Echo360 titles are typically raw section codes like "COSC440-25S1-Lecture AA-Deep Learning" where every lecture has the same useless title (the section name).

You MUST respond with a single JSON object:
{
  "course_name": "<short clean course name>",
  "lectures": [
    {"id": <lecture_id>, "title": "<clean title>"},
    ...
  ]
}

Rules:
- Extract a clean course name: human-readable name followed by the course code. E.g. "COSC440-25S1-Lecture AA-Deep Learning" → "Deep Learning COSC440"
- Strip semester codes (25S1, 25W), section letters (Lecture AA, LecA), but KEEP the course code (e.g. COSC440, SENG402).
- If all lectures have the same title (the section name), number them sequentially by date: "Lecture 1", "Lecture 2", etc.
- If lectures have unique meaningful info beyond the section code, preserve it: "Lecture 3 - Neural Networks"
- Keep it simple and concise.
- Return ALL lectures from the input, preserving their IDs exactly.

Your entire response must be valid JSON. No text before or after."""

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "title_cleanup",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "course_name": {
                    "type": "string",
                    "description": "Clean course name",
                },
                "lectures": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "title": {"type": "string"},
                        },
                        "required": ["id", "title"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["course_name", "lectures"],
            "additionalProperties": False,
        },
    },
}

RESPONSE_FORMAT_JSON = {"type": "json_object"}


async def clean_titles(course_id: int) -> None:
    """Use an LLM to clean up course + lecture titles."""
    with get_db() as session:
        course = session.get(Course, course_id)
        if not course:
            return
        course_name = course.name
        lectures = (
            session.query(Lecture.id, Lecture.title, Lecture.date)
            .filter(Lecture.course_id == course_id)
            .order_by(Lecture.date.asc())
            .all()
        )
        if not lectures:
            return

    lecture_list = [{"id": lid, "title": title, "date": date} for lid, title, date in lectures]
    user_msg = json.dumps({"course_name": course_name, "lectures": lecture_list})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        jobs.broadcast({"type": "titles_fixing", "course_id": course_id})

        _LOGGER.info("Calling router for title cleanup")
        kwargs = {
            "model": "titles",
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.1,
            "response_format": RESPONSE_SCHEMA,
        }
        try:
            response = await router.acompletion(**kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if "response_format" in err_str or "json_schema" in err_str or "schema" in err_str:
                _LOGGER.info("json_schema not supported, retrying with json_object")
                kwargs["response_format"] = RESPONSE_FORMAT_JSON
                response = await router.acompletion(**kwargs)
            else:
                raise

        content = response.choices[0].message.content
        if not content or not content.strip():
            _LOGGER.warning("Empty response from %s, falling back", response.model)
            # Fall back to json_object format in case json_schema caused the empty response
            kwargs["response_format"] = RESPONSE_FORMAT_JSON
            response = await router.acompletion(**kwargs)
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise RuntimeError("Empty response from LLM")

        result = _parse_response(content)
        _LOGGER.info("Title cleanup succeeded with model: %s", response.model)

        with get_db() as session:
            course = session.get(Course, course_id)
            if course:
                course.display_name = result["course_name"]

            lecture_map = {item["id"]: item["title"] for item in result["lectures"]}
            for lec in session.query(Lecture).filter(Lecture.course_id == course_id).all():
                if lec.id in lecture_map:
                    lec.title = lecture_map[lec.id]

        _LOGGER.info("Titles cleaned for course %d", course_id)
        jobs.broadcast({"type": "titles_fixed", "course_id": course_id})

    except Exception as e:
        _LOGGER.exception("Title cleanup failed for course %d", course_id)
        jobs.broadcast({"type": "titles_error", "course_id": course_id, "error": str(e)})


def _parse_response(raw: str) -> dict:
    """Parse the LLM JSON response."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.index("\n")
        text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    data = json.loads(text)
    if "course_name" not in data or "lectures" not in data:
        raise ValueError("Missing required fields in response")
    return data


