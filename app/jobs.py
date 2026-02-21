"""Background job queue and SSE broadcast."""
import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

_LOGGER = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_listeners: list[asyncio.Queue] = []
_lock = threading.Lock()

# Semaphore to cap concurrent downloads (async tasks, not threads)
_download_sem: asyncio.Semaphore | None = None
_transcribe_sem: asyncio.Semaphore | None = None
_tasks: set[asyncio.Task] = set()

# Thread pool for Selenium (the only truly blocking work left)
_blocking_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="echo360-blocking")


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


async def start_workers(max_concurrent_downloads: int = 10, max_concurrent_transcriptions: int = 2) -> None:
    """Initialise concurrency semaphores. Called from lifespan."""
    global _download_sem, _transcribe_sem
    _download_sem = asyncio.Semaphore(max_concurrent_downloads)
    _transcribe_sem = asyncio.Semaphore(max_concurrent_transcriptions)
    _LOGGER.info("Download concurrency limit: %d, transcription limit: %d", max_concurrent_downloads, max_concurrent_transcriptions)


def broadcast(data: dict) -> None:
    """Thread-safe push to all active SSE listeners."""
    if _loop is None:
        return
    msg = json.dumps(data)
    with _lock:
        listeners = list(_listeners)
    for q in listeners:
        _loop.call_soon_threadsafe(q.put_nowait, msg)


async def listen() -> AsyncIterator[str]:
    """Async generator consumed by the SSE endpoint."""
    q: asyncio.Queue[str] = asyncio.Queue()
    with _lock:
        _listeners.append(q)
    try:
        while True:
            yield await q.get()
    finally:
        with _lock:
            if q in _listeners:
                _listeners.remove(q)


def enqueue_download(lecture_id: int, output_dir: str) -> None:
    """Fire an async download task, gated by the concurrency semaphore."""
    from app import pipeline

    async def _run():
        async with _download_sem:
            try:
                await pipeline.run_download(lecture_id, output_dir)
            except Exception:
                _LOGGER.exception("Download failed for lecture %d", lecture_id)

    if _loop is not None and _download_sem is not None:
        _schedule(_run())


def enqueue_convert(lecture_id: int, raw_path: str, output_dir: str, filename: str) -> None:
    """Schedule an async conversion task on the event loop."""
    from app import pipeline

    async def _run():
        try:
            await pipeline.run_convert(lecture_id, raw_path, output_dir, filename)
        except Exception:
            _LOGGER.exception("Conversion failed for lecture %d", lecture_id)

    _schedule(_run())


def enqueue_transcribe(lecture_id: int, model_name: str) -> None:
    """Schedule an async transcription task, gated by the transcription semaphore."""
    from app import transcriber

    async def _run():
        async with _transcribe_sem:
            try:
                await transcriber.transcribe_lecture(lecture_id, model_name)
            except Exception:
                _LOGGER.exception("Transcription failed for lecture %d", lecture_id)

    if _loop is not None and _transcribe_sem is not None:
        _schedule(_run())


def _schedule(coro) -> None:
    """Schedule a coroutine as a fire-and-forget task on the event loop."""
    if _loop is None:
        return

    def _create():
        task = _loop.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    _loop.call_soon_threadsafe(_create)


def submit(fn, *args, **kwargs):
    """Submit blocking work (e.g. sync_course) to the blocking executor."""
    return _blocking_executor.submit(fn, *args, **kwargs)


def shutdown() -> None:
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    _blocking_executor.shutdown(wait=False, cancel_futures=True)
