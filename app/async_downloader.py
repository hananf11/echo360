"""Async HLS downloader for the web app (replaces gevent-based Downloader)."""
import asyncio
import logging
import os
import tempfile
from typing import Callable
from urllib.parse import urlparse

import httpx

from echo360.naive_m3u8_parser import NaiveM3U8Parser

_LOGGER = logging.getLogger(__name__)
_SEM = asyncio.Semaphore(30)


def _urljoin(base: str, relative: str) -> str:
    base = base[: base.rfind("/") + 1]
    while relative.startswith("/"):
        relative = relative[1:]
    return base + relative


async def resolve_audio_m3u8(client: httpx.AsyncClient, m3u8_url: str) -> list[str]:
    """Fetch a master M3U8, find the audio stream, return segment URLs."""
    r = await client.get(m3u8_url, timeout=20)
    r.raise_for_status()
    lines = r.text.split("\n")

    parser = NaiveM3U8Parser(lines)
    parser.parse()
    m3u8_video, m3u8_audio = parser.get_video_and_audio()

    audio_url = _urljoin(m3u8_url, m3u8_audio) if m3u8_audio else (
        _urljoin(m3u8_url, m3u8_video) if m3u8_video else None
    )
    if not audio_url:
        raise RuntimeError("No audio stream found in M3U8")

    # Fetch the segment-level playlist
    r = await client.get(audio_url, timeout=20)
    r.raise_for_status()
    segments = [
        _urljoin(audio_url, line.strip())
        for line in r.text.split("\n")
        if line.strip() and not line.startswith("#")
    ]
    # If we got a single sub-playlist instead of segments, resolve one more level
    if len(segments) == 1 and not segments[0].split("?")[0].split(".")[-1] in ("ts", "mp4", "m4s"):
        nested_url = segments[0]
        r = await client.get(nested_url, timeout=20)
        r.raise_for_status()
        segments = [
            _urljoin(nested_url, line.strip())
            for line in r.text.split("\n")
            if line.strip() and not line.startswith("#")
        ]
    return segments


async def download_segments(
    client: httpx.AsyncClient,
    segments: list[str],
    output_dir: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Download all HLS segments and join them into a single .ts file. Returns the path."""
    os.makedirs(output_dir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(dir=output_dir)
    total = len(segments)
    done = 0
    results: dict[int, bytes] = {}

    async def _fetch(idx: int, url: str):
        nonlocal done
        async with _SEM:
            for attempt in range(3):
                try:
                    r = await client.get(url, timeout=30)
                    r.raise_for_status()
                    results[idx] = r.content
                    done += 1
                    if on_progress:
                        on_progress(done, total)
                    return
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1)

    async with asyncio.TaskGroup() as tg:
        for i, url in enumerate(segments):
            tg.create_task(_fetch(i, url))

    # Join segments in order
    ext = segments[0].split("?")[0].split(".")[-1] if segments else "ts"
    joined_path = os.path.join(tmp_dir, f"joined.{ext}")
    with open(joined_path, "wb") as out:
        for i in range(total):
            out.write(results[i])

    # Move to output dir
    final_path = os.path.join(output_dir, f"raw_download.{ext}")
    os.rename(joined_path, final_path)
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass
    return final_path


async def download_direct(
    client: httpx.AsyncClient,
    url: str,
    output_dir: str,
    filename: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Stream-download a direct file URL. Returns the path."""
    os.makedirs(output_dir, exist_ok=True)
    ext = url.split("?")[0].split(".")[-1]
    out_path = os.path.join(output_dir, f"{filename}_raw.{ext}")

    async with client.stream("GET", url, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(out_path, "wb") as f:
            async for chunk in r.aiter_bytes(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total:
                    on_progress(downloaded, total)

    return out_path
