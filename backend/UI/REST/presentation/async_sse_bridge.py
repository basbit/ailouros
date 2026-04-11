"""Bridge: converts a sync generator into an async iterator with client-disconnect detection.

By default Starlette StreamingResponse wraps sync iterables in
iterate_in_threadpool: each next() may come from a different worker thread.
The chain run_pipeline_stream -> ThreadPoolExecutor (LLM step) is not safe in
that case and causes hangs.

This module drives the entire sync generator from a single dedicated thread and
exposes it as an async iterator.  A threading.Event (cancel_event) is forwarded
to the pipeline so that on app shutdown steps stop without blocking on
executor.__exit__.
"""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator, Callable, Generator
from typing import Any

from fastapi import Request

from backend.App.orchestration.domain.exceptions import PipelineCancelled
from backend.App.orchestration.infrastructure.stream_cancel import (
    register_task_cancel_event,
    unregister_task_cancel_event,
)

import logging

logger = logging.getLogger(__name__)

# Tracks running pipeline sentinel tasks for graceful shutdown.
# Populated here; re-exported by stream_handlers for the lifespan handler.
_active_tasks: set[asyncio.Task] = set()


async def sync_to_async_sse(
    request: Request,
    chunks_factory: Callable[[threading.Event], Generator[str, None, None]],
    task_id: str = "",
) -> AsyncIterator[str]:
    """Drive the entire sync generator from a single thread.

    Args:
        request: The FastAPI/Starlette ``Request`` used for disconnect detection.
        chunks_factory: Callable that receives a ``threading.Event`` (cancel signal)
            and returns a sync generator yielding SSE data lines.
        task_id: Optional task identifier used to register the cancel event so
            external cancel endpoints can interrupt the pipeline.

    Yields:
        SSE ``data: ...\\n\\n`` string chunks as they are produced by the generator.
    """
    cancel_ev = threading.Event()
    if task_id:
        register_task_cancel_event(task_id, cancel_ev)
    chunks = chunks_factory(cancel_ev)
    q: queue.Queue[tuple[str, Any]] = queue.Queue()
    _done = object()

    def _producer() -> None:
        try:
            for line in chunks:
                q.put(("chunk", line))
        except BaseException as exc:
            q.put(("error", exc))
        finally:
            q.put(("done", _done))

    threading.Thread(target=_producer, name="swarm-pipeline-sse", daemon=True).start()

    # Register a sentinel asyncio.Task so lifespan shutdown can wait for active pipelines.
    _sentinel_done = asyncio.get_running_loop().create_future()

    async def _pipeline_sentinel() -> None:
        await _sentinel_done

    sentinel_task = asyncio.create_task(_pipeline_sentinel())
    _active_tasks.add(sentinel_task)
    sentinel_task.add_done_callback(_active_tasks.discard)

    try:
        while True:
            if await request.is_disconnected():
                cancel_ev.set()
                break
            try:
                item = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            kind, payload = item

            if kind == "chunk":
                yield str(payload)
            elif kind == "error":
                if isinstance(payload, BaseException):
                    if isinstance(payload, PipelineCancelled):
                        logger.info("SSE pipeline cancelled (thread): %s", payload)
                        return
                    raise payload
                raise RuntimeError(f"pipeline thread failed: {payload!r}")
            else:
                break
    finally:
        cancel_ev.set()
        if task_id:
            unregister_task_cancel_event(task_id)
        if not _sentinel_done.done():
            _sentinel_done.set_result(None)
