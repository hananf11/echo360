"""Background job queue and SSE broadcast."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

_loop: asyncio.AbstractEventLoop | None = None
_listeners: list[asyncio.Queue] = []
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="echo360-worker")


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


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


def submit(fn, *args, **kwargs):
    return _executor.submit(fn, *args, **kwargs)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
