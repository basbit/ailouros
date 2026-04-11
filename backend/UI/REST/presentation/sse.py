"""SSE (Server-Sent Events) utilities for the UI/REST layer.

Canonical location: backend/UI/REST/presentation/sse.py.
``orchestrator/sse.py`` and ``orchestrator/presentation/sse_utils.py`` are kept
as re-export shims for backward compatibility.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi.responses import StreamingResponse


class _DirectSSEResponse(StreamingResponse):
    """SSE response without listen_for_disconnect.

    Starlette 0.27+ with spec_version < 2.4 (uvicorn HTTP = "2.3") starts
    listen_for_disconnect in parallel via anyio task group and calls
    ``await receive()`` simultaneously with chunk delivery.  In some
    configurations this blocks SSE event delivery until connection close.

    This class always uses the direct path (like spec_version >= 2.4):
    stream_response without parallel receive().  Pipeline cancellation on client
    disconnect is handled via finally block in _sync_sse_generator_to_async ->
    cancel_ev.set().
    """

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await self.stream_response(send)
        except OSError:
            pass  # client disconnected mid-stream -- pipeline cancel_ev handles cleanup
        if self.background is not None:
            await self.background()


try:
    _SSE_DELTA_MAX_CHARS = int(os.getenv("SWARM_SSE_DELTA_MAX_CHARS", "64000"))
except ValueError:
    _SSE_DELTA_MAX_CHARS = 64000
_SSE_DELTA_MAX_CHARS = max(4096, min(_SSE_DELTA_MAX_CHARS, 500_000))


def _truncate_for_sse_delta(text: str, *, max_chars: int = _SSE_DELTA_MAX_CHARS) -> str:
    """Truncate large completed bodies that cause client encode/send hangs."""
    s = str(text or "")
    if len(s) <= max(4096, max_chars):
        return s
    cap = max(4096, max_chars)
    return (
        s[: cap - 120]
        + "\n\n... [truncated in SSE; full text: artifacts/<task>/agents/<agent>.txt]\n"
    )


def _sse_delta_line(now: int, request_model: str, content: str) -> str:
    payload = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_agent_sse_event(
    now: int, model: str, agent: str, status: str, message: str
) -> str:
    """Return a single SSE ``data: ...\\n\\n`` line for an agent pipeline event."""
    stream_body = _truncate_for_sse_delta(f"[{agent}] {status}: {message}\n")
    payload = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": stream_body,
                },
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ensure_task_dirs(task_dir: Path, agents_dir: Path) -> None:
    """Ensure task directories exist (TTL-cleanup or shutdown may have removed them during stream)."""
    task_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
