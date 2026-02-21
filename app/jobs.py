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

# Async download queue — processed by persistent worker tasks
_download_queue: asyncio.Queue | None = None
_worker_tasks: list[asyncio.Task] = []

# Thread pools for truly blocking work
_blocking_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="echo360-blocking")
_convert_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="echo360-convert")
_transcribe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo360-transcribe")


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


async def start_workers(num_download_workers: int = 3) -> None:
    """Start persistent async download workers. Called from lifespan."""
    global _download_queue
    _download_queue = asyncio.Queue()
    for i in range(num_download_workers):
        task = asyncio.create_task(_download_worker(i))
        _worker_tasks.append(task)
    _LOGGER.info("Started %d download workers", num_download_workers)


async def _download_worker(worker_id: int) -> None:
    """Persistent worker that pulls from the download queue."""
    from app import pipeline

    while True:
        try:
            lecture_id, output_dir = await _download_queue.get()
            try:
                await pipeline.run_download(lecture_id, output_dir)
            except Exception:
                _LOGGER.exception("Download worker %d: failed lecture %d", worker_id, lecture_id)
            finally:
                _download_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            _LOGGER.exception("Download worker %d: unexpected error", worker_id)


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
    """Add a download to the async queue."""
    if _download_queue is not None and _loop is not None:
        _loop.call_soon_threadsafe(_download_queue.put_nowait, (lecture_id, output_dir))


def enqueue_convert(lecture_id: int, raw_path: str, output_dir: str, filename: str) -> None:
    """Submit a conversion job to the thread executor."""
    from app import pipeline
    _convert_executor.submit(pipeline.run_convert, lecture_id, raw_path, output_dir, filename)


def submit_transcribe(fn, *args, **kwargs):
    return _transcribe_executor.submit(fn, *args, **kwargs)


def submit(fn, *args, **kwargs):
    """Submit blocking work (e.g. sync_course) to the blocking executor."""
    return _blocking_executor.submit(fn, *args, **kwargs)


def shutdown() -> None:
    for task in _worker_tasks:
        task.cancel()
    _worker_tasks.clear()
    _blocking_executor.shutdown(wait=False, cancel_futures=True)
    _convert_executor.shutdown(wait=False, cancel_futures=True)
    _transcribe_executor.shutdown(wait=False, cancel_futures=True)
