from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)

_active_tasks: set[asyncio.Task] = set()


async def sync_to_async_sse(
    request: Request,
    chunks_factory: Callable[[threading.Event], Generator[str, None, None]],
    task_id: str = "",
) -> AsyncIterator[str]:
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
