"""Single source of SSE payload construction.

All SSE ``data: ...\\n\\n`` lines for the streaming pipeline are built here.
No business logic lives here — only JSON serialization helpers.
"""
from __future__ import annotations

import json
import time
from typing import Any


def _base_chunk(task_id: str, model: str, now: int | None = None) -> dict[str, Any]:
    ts = now if now is not None else int(time.time())
    return {
        "id": f"chatcmpl-{task_id or ts}",
        "object": "chat.completion.chunk",
        "created": ts,
        "model": model,
    }


def build_agent_event(
    now: int,
    model: str,
    agent: str,
    status: str,
    message: str,
    *,
    truncate_fn: Any = None,
    **extra: Any,
) -> str:
    """Return a complete SSE data line for an agent pipeline event.

    Args:
        now: Unix timestamp integer (reuse caller's ``now`` for consistency).
        model: Model identifier string sent to the client.
        agent: Agent role name (e.g. "dev", "qa").
        status: Status string (e.g. "completed", "in_progress").
        message: Agent output message.
        truncate_fn: Optional callable to truncate large message bodies.
        **extra: Additional top-level keys to merge into the payload.

    Returns:
        A complete ``data: {...}\\n\\n`` SSE line.
    """
    body = f"[{agent}] {status}: {message}\n"
    if truncate_fn is not None:
        body = truncate_fn(body)
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": body},
                "finish_reason": None,
            }
        ],
    }
    payload.update(extra)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_delta(now: int, model: str, text: str) -> str:
    """Return an incremental text chunk SSE event.

    Args:
        now: Unix timestamp integer.
        model: Model identifier string.
        text: Incremental content to stream to the client.

    Returns:
        A complete ``data: {...}\\n\\n`` SSE line.
    """
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_done(now: int, model: str) -> str:
    """Return a stream completion SSE event.

    Args:
        now: Unix timestamp integer.
        model: Model identifier string.

    Returns:
        A complete ``data: {...}\\n\\n`` SSE line with ``finish_reason: stop``.
    """
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_error(now: int, model: str, message: str) -> str:
    """Return an error SSE event (delta with error content, finish_reason None).

    Args:
        now: Unix timestamp integer.
        model: Model identifier string.
        message: Error description text.

    Returns:
        A complete ``data: {...}\\n\\n`` SSE line.
    """
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": message},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_extra_event(now: int, model: str, **extra_fields: Any) -> str:
    """Return an SSE event with empty delta content and extra top-level fields.

    Used for metadata events such as ``session_preflight``, ``mcp_preflight``,
    ``workspace_index_stats``, and ``retry_requested``.

    Args:
        now: Unix timestamp integer.
        model: Model identifier string.
        **extra_fields: Extra top-level keys merged into the payload.

    Returns:
        A complete ``data: {...}\\n\\n`` SSE line.
    """
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": None}],
    }
    payload.update(extra_fields)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
