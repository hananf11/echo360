# AI Lecture Notes Tool — Implementation Plan

## Overview

Extend the echo360 downloader into an AI-powered lecture notes tool. The primary goal is **audio extraction and transcription** — transcripts are the most valuable output. Video frame extraction is planned for a later phase.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Web UI (Browser)                │
│  Course Library → Lecture List → Notes Viewer   │
└────────────────────┬────────────────────────────┘
                     │ HTTP (localhost)
┌────────────────────▼────────────────────────────┐
│              FastAPI Backend                     │
│  /courses  /lectures  /jobs  /notes  /config    │
└──────┬─────────────┬──────────────┬─────────────┘
       │             │              │
┌──────▼──────┐ ┌───▼──────┐ ┌────▼──────────────┐
│  SQLite DB  │ │ Job Queue│ │  File Storage      │
│  (metadata) │ │ (threads)│ │  audio/ transcripts│
└─────────────┘ └──────────┘ │  notes/            │
                              └───────────────────┘
       │                  │              │
┌──────▼──────┐  ┌────────▼──────┐ ┌────▼──────────┐
│ Echo360     │  │faster-whisper │ │ LLM Backend   │
│ Scraper     │  │(transcription)│ │ (configurable)│
│ (existing)  │  └───────────────┘ └───────────────┘
└─────────────┘
```

## Phased Delivery

| Phase | What you get | Effort |
|-------|-------------|--------|
| 1 | Audio-only download, `.opus` output | Small |
| 2 | Web UI shell, course sync, lecture library | Medium |
| 3 | Background jobs, live status updates | Medium |
| 4 | Auto-transcription after download | Small |
| 5 | LLM notes generation, Markdown + HTML viewer | Medium |
| 6 | Frame extraction (future) | Later |

---

## Phase 1 — Audio-Only Download Pipeline

**Goal:** Strip the downloader to audio-only output. The M3U8 parser already separates audio and video streams — we skip the video download and the `combine_audio_video()` call.

**Changes to existing code:**

- `echo360/hls_downloader.py` / `echo360/videos.py`: Add `audio_only=True` mode — download only the `m3u8_audio` stream, skip `m3u8_video` and `combine_audio_video()`
- Post-download: pipe through `ffmpeg` to transcode raw `.ts` segments into **Opus** (`.opus`) — excellent quality at ~64kbps, ~28MB/hour of audio
- Store originals as `.opus`; never delete them so reprocessing with a better model later is trivial

**CLI usage:**
```bash
python echo360.py URL --audio-only
```

**Deliverable:** Produces a small `.opus` file per lecture.

---

## Phase 2 — Database + Web App Shell

**Goal:** FastAPI web server with SQLite backing, replacing the CLI as the primary interface.

**Stack:**
- **FastAPI** — async Python, handles background tasks cleanly
- **SQLite** (raw `sqlite3`) — no server, no ORM overhead
- **Jinja2 + HTMX** — server-rendered HTML with lightweight dynamic updates, no JS build step

**Database schema:**

```sql
courses      (id, name, url, echo_section_id, last_synced_at)

lectures     (id, course_id, echo_id, title, date, duration_secs,
              audio_path, audio_status)
              -- audio_status: pending | downloading | done | error

transcripts  (id, lecture_id, model, content_json, created_at)
              -- content_json: [{start, end, text}, ...] timestamped segments

notes        (id, lecture_id, llm, content_md, content_html, created_at)

jobs         (id, lecture_id, type, status, progress, error, created_at)
              -- type: download | transcribe | generate_notes
```

**UI pages:**
1. **`/`** — Course library: cards per course, "Sync" button, lecture count, last synced time
2. **`/courses/<id>`** — Lecture list: title, date, status badges (downloaded / transcribed / notes ready), bulk "Download All" button
3. **`/lectures/<id>`** — Detail page: transcript viewer, notes viewer (rendered Markdown), job log
4. **`/settings`** — LLM backend config, storage path, Whisper model selection

**Deliverable:** Open `localhost:8000`, see courses, browse lectures, see status.

---

## Phase 3 — Background Job System

**Goal:** Downloads and processing run in the background; UI stays responsive with live progress.

**Design:**
- `JobQueue` class backed by a `threading.Thread` worker pool (2–4 workers) — avoids Celery/Redis complexity
- Jobs persisted in the `jobs` SQLite table so they survive server restarts
- UI polls `/api/jobs/active` every 2s via HTMX to refresh status badges without full page reloads
- Job dependency chain: `download` → `transcribe` → `generate_notes` (each auto-enqueues the next on completion)
- "Download All" enqueues one `download` job per lecture in the course

**Deliverable:** Click "Download All", watch lecture cards tick grey → yellow (downloading) → green (done) in real time.

---

## Phase 4 — Transcription

**Goal:** Run faster-whisper on each `.opus` file and store timestamped segments.

**Design:**
- `faster-whisper` (CTranslate2-backed) — significantly faster than original Whisper, same accuracy
- Default model: `medium` (speed/accuracy balance); switchable to `large-v3` in settings for higher-accuracy reprocessing later
- Transcript stored as JSON array of `{start, end, text}` segments in `transcripts.content_json`
- Also writes a `.txt` sidecar file alongside the audio for easy external access
- The `transcribe` worker loads the model once and reuses it across the queue (model load is expensive)

**Deliverable:** After download completes, transcription runs automatically; transcript with timestamps appears in the lecture detail page.

---

## Phase 5 — Note Generation

**Goal:** LLM converts the raw transcript into structured lecture notes, rendered as Markdown and HTML.

### LLM Abstraction Layer (`app/notes/backends.py`)

```python
class LLMBackend(Protocol):
    def complete(self, system: str, user: str) -> str: ...

class ClaudeBackend:    # anthropic SDK
class OpenAIBackend:    # openai SDK
class OllamaBackend:    # local HTTP API (no API key needed)
```

Configured via `settings.json` — backend name, model name, and API key if required.

**Note generation pipeline:**
- Transcript segments chunked to fit context window
- Structured prompt requests: summary, key concepts, definitions, equations/diagrams mentioned, open questions
- Output is **Markdown** — saved as `notes/<lecture_id>.md`
- Server-side rendered to HTML using `python-markdown` with extensions (tables, fenced code, toc) — saved as `notes/<lecture_id>.html`
- Lecture detail page shows a two-tab view: **Raw Transcript** | **Structured Notes**

**Deliverable:** After transcription, notes generate automatically and render cleanly in the browser.

---

## Docker Deployment

**Goal:** Single-command stack spin-up so the full app (backend, frontend, Chromium, ffmpeg) runs in a reproducible container.

**Design:**
- Multi-stage Dockerfile: `node:20-alpine` builds the React frontend, `python:3.12-slim` is the runtime
- Runtime image installs: `chromium`, `chromium-driver`, `ffmpeg`, Python deps from `requirements.txt`
- `CHROME_BIN` / `CHROMEDRIVER_PATH` env vars point the existing Selenium code at the system Chromium
- Two named volumes: `echo360-data` (SQLite DB + audio files), bind-mounted `_browser_persistent_session/` (cookies)
- All configuration via environment variables (`ECHO360_DB`, `ECHO360_AUDIO_DIR`)

**Usage:**
```bash
# Build and start
docker compose up --build

# Just start (after first build)
docker compose up

# Run in background
docker compose up -d
```

Open `http://localhost:8000`. First time you add a course the scraper will need to log in — a Chrome window will not be visible (headless), so the session cookie flow handles authentication.

**Files:** `Dockerfile`, `docker-compose.yml`

---

## Phase 6 — Frame Extraction (Future)

Placeholder design for a later phase:

- After notes are generated, a separate job asks the LLM: *"Given this transcript, list timestamps where a visual aid (diagram, equation, or slide change) was being discussed"*
- Those timestamps are fed to `ffmpeg` to extract frames — either from a separately downloaded video, or by triggering a targeted video-only download for just those segments
- Extracted frames are embedded inline into the notes HTML at the relevant positions

---

## Open Questions

- **Authentication**: Does the Echo360 instance use SSO (university login) or direct username/password? The existing Selenium-based login needs to work headlessly from within the web app.
- **Storage location**: Default storage path for audio/transcripts/notes (e.g. `~/echo360-data/`).
- **CLI preservation**: Should Phase 1 modify the existing CLI in-place, or should the new system be built alongside it keeping the original untouched?
