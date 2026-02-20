# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Echo360 Videos Downloader — a CLI tool that downloads lecture videos from university Echo360 lecture recording systems and echo360.org/net cloud platforms using Selenium webdriver, HLS streaming downloads, and optional ffmpeg transcoding.

## Setup & Running

```bash
# Automated (creates venv, installs deps)
./run.sh "COURSE_URL"

# Manual
pip install -r requirements.txt
python echo360.py "COURSE_URL"
```

No test suite exists — testing is manual/integration-based. Use `--debug` to enable verbose logging to `echo360Downloader.log`.

## Common Commands

```bash
# Basic download
python echo360.py "https://view.streaming.sydney.edu.au:8443/ess/portal/section/UUID"

# Interactive video selection with Chrome
python echo360.py "URL" --interactive --chrome

# Date range filter
python echo360.py "URL" --after-date 2024-01-01 --before-date 2024-05-31 --output ~/Lectures

# Store credentials for reuse
python echo360.py "URL" --persistent-session --setup-credentials

# Build standalone executable
pip install pyinstaller && python -m PyInstaller echo360.spec
```

## Architecture

```
main.py (argparse CLI)
    └─> EchoDownloader (downloader.py) — main orchestrator
        ├─> EchoCourse / EchoCloudCourse (course.py) — course metadata via REST API
        ├─> Selenium webdriver — handles auth (SSO) and page navigation
        ├─> EchoVideos / EchoVideo (videos.py) — extracts M3U8 URLs from video player
        └─> Downloader (hls_downloader.py) — parallel HLS segment downloads via gevent
            ├─> NaiveM3U8Parser (naive_m3u8_parser.py) — parses M3U8 playlists
            └─> ffmpy — optional transcoding of .ts segments to MP4
```

**Two course types:**
- `EchoCourse` — traditional university Echo360 systems (e.g., `view.streaming.sydney.edu.au`)
- `EchoCloudCourse` — modern cloud platform (`echo360.org`, `echo360.net`)

**Webdriver options:** PhantomJS (default), Chrome (`--chrome`), Firefox (`--firefox`), undetected-chromedriver (`--stealth`). The `binary_downloader/` subpackage manages automatic webdriver binary downloads.

**HLS downloading** uses gevent greenlets for parallel segment fetching. Videos are saved as `.ts` files and optionally transcoded to `.mp4` via ffmpeg.

## Key Files

| File | Purpose |
|------|---------|
| `echo360/downloader.py` | `EchoDownloader` — top-level workflow orchestration |
| `echo360/course.py` | `EchoCourse` / `EchoCloudCourse` — API wrappers |
| `echo360/videos.py` | `EchoVideos` / `EchoVideo` — video URL extraction |
| `echo360/hls_downloader.py` | HLS segment downloader with gevent |
| `echo360/naive_m3u8_parser.py` | M3U8 playlist parser |
| `echo360/binary_downloader/` | Webdriver binary management |
