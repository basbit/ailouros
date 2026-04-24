"""TEST-02: SSE stream cancel event behaviour."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock


def test_cancel_event_set_after_generator_exhausted():
    """cancel_ev.set() is called in the finally block once the async generator exits."""
    from backend.App.shared.infrastructure.rest.sse_bridge import sync_to_async_sse as _sync_sse_generator_to_async

    captured_events: list[threading.Event] = []

    def factory(cancel_ev: threading.Event):
        captured_events.append(cancel_ev)
        yield "line1\n"
        yield "line2\n"

    async def run():
        req = MagicMock()
        req.is_disconnected = AsyncMock(return_value=False)
        collected = []
        async for chunk in _sync_sse_generator_to_async(req, factory):
            collected.append(chunk)
        return collected

    chunks = asyncio.run(run())
    assert chunks == ["line1\n", "line2\n"]
    assert len(captured_events) == 1
    assert captured_events[0].is_set(), "cancel_ev must be set after generator exits"


def test_cancel_event_set_even_on_early_break():
    """cancel_ev is set even if the consumer stops reading mid-stream (break)."""
    from backend.App.shared.infrastructure.rest.sse_bridge import sync_to_async_sse as _sync_sse_generator_to_async

    captured_events: list[threading.Event] = []

    def factory(cancel_ev: threading.Event):
        captured_events.append(cancel_ev)
        yield "a"
        yield "b"
        yield "c"

    async def run():
        req = MagicMock()
        req.is_disconnected = AsyncMock(return_value=False)
        collected = []
        async for chunk in _sync_sse_generator_to_async(req, factory):
            collected.append(chunk)
            break  # early exit
        return collected

    asyncio.run(run())
    assert len(captured_events) == 1
    assert captured_events[0].is_set()


def test_pipeline_should_cancel_responds_to_event():
    """_pipeline_should_cancel returns True when cancel_event is set."""
    from backend.App.orchestration.application.routing.pipeline_graph import _pipeline_should_cancel

    ev = threading.Event()
    state = {"_pipeline_cancel_event": ev}

    assert not _pipeline_should_cancel(state), "should be False before set()"
    ev.set()
    assert _pipeline_should_cancel(state), "should be True after set()"


def test_pipeline_should_cancel_no_event():
    """_pipeline_should_cancel returns False when no cancel_event in state."""
    from backend.App.orchestration.application.routing.pipeline_graph import _pipeline_should_cancel

    assert not _pipeline_should_cancel({})
    assert not _pipeline_should_cancel({"_pipeline_cancel_event": None})


def test_pipeline_should_cancel_server_shutdown(monkeypatch):
    """_pipeline_should_cancel returns True when SERVER_STREAM_SHUTDOWN is set."""
    from backend.App.orchestration.infrastructure.stream_cancel import clear_stream_shutdown, mark_stream_shutdown_start
    from backend.App.orchestration.application.routing.pipeline_graph import _pipeline_should_cancel

    clear_stream_shutdown()
    assert not _pipeline_should_cancel({})

    mark_stream_shutdown_start()
    try:
        assert _pipeline_should_cancel({})
    finally:
        clear_stream_shutdown()
