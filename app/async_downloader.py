"""Async HLS downloader for the web app (replaces gevent-based Downloader)."""
import asyncio
import logging
import os
import tempfile
from typing import Callable

import httpx
import m3u8

_LOGGER = logging.getLogger(__name__)
_SEM = asyncio.Semaphore(30)


async def resolve_audio_m3u8(client: httpx.AsyncClient, m3u8_url: str) -> list[str]:
    """Fetch a master M3U8, find the audio stream, return segment URLs."""
    r = await client.get(m3u8_url, timeout=20)
    r.raise_for_status()
    playlist = m3u8.loads(r.text, uri=m3u8_url)

    # Find audio media entry, or fall back to last variant playlist
    audio_url = None
    for media in playlist.media:
        if media.type == "AUDIO" and media.uri:
            audio_url = media.absolute_uri
            break
    if not audio_url and playlist.playlists:
        audio_url = playlist.playlists[-1].absolute_uri
    if not audio_url:
        raise RuntimeError("No audio stream found in M3U8")

    # Fetch the segment-level playlist
    r = await client.get(audio_url, timeout=20)
    r.raise_for_status()
    seg_playlist = m3u8.loads(r.text, uri=audio_url)

    # Handle nested playlists (another level of indirection)
    if not seg_playlist.segments and seg_playlist.playlists:
        nested_url = seg_playlist.playlists[0].absolute_uri
        r = await client.get(nested_url, timeout=20)
        r.raise_for_status()
        seg_playlist = m3u8.loads(r.text, uri=nested_url)

    return [seg.absolute_uri for seg in seg_playlist.segments]


async def resolve_video_m3u8(client: httpx.AsyncClient, m3u8_url: str) -> list[tuple[str, float]]:
    """Fetch a master M3U8, find the highest-quality video variant, return (segment_url, duration) tuples."""
    r = await client.get(m3u8_url, timeout=20)
    r.raise_for_status()
    playlist = m3u8.loads(r.text, uri=m3u8_url)

    # Pick highest-quality video variant (last in list = highest bitrate)
    video_url = None
    if playlist.playlists:
        video_url = playlist.playlists[-1].absolute_uri
    if not video_url:
        raise RuntimeError("No video variant found in M3U8")

    # Fetch the segment-level playlist
    r = await client.get(video_url, timeout=20)
    r.raise_for_status()
    seg_playlist = m3u8.loads(r.text, uri=video_url)

    # Handle nested playlists (another level of indirection)
    if not seg_playlist.segments and seg_playlist.playlists:
        nested_url = seg_playlist.playlists[0].absolute_uri
        r = await client.get(nested_url, timeout=20)
        r.raise_for_status()
        seg_playlist = m3u8.loads(r.text, uri=nested_url)

    return [(seg.absolute_uri, seg.duration) for seg in seg_playlist.segments]


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
