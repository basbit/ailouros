from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    _SSE_DELTA_MAX_CHARS = int(os.getenv("SWARM_SSE_DELTA_MAX_CHARS", "64000"))
except ValueError:
    _SSE_DELTA_MAX_CHARS = 64000
_SSE_DELTA_MAX_CHARS = max(4096, min(_SSE_DELTA_MAX_CHARS, 500_000))


def truncate_for_sse_delta(text: str, *, max_chars: int = _SSE_DELTA_MAX_CHARS) -> str:
    s = str(text or "")
    if len(s) <= max(4096, max_chars):
        return s
    cap = max(4096, max_chars)
    return (
        s[: cap - 120]
        + "\n\n... [truncated in SSE; full text: artifacts/<task>/agents/<agent>.txt]\n"
    )


def sse_delta_line(now: int, request_model: str, content: str) -> str:
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


def build_agent_sse_event(
    now: int, model: str, agent: str, status: str, message: str
) -> str:
    stream_body = truncate_for_sse_delta(f"[{agent}] {status}: {message}\n")
    payload = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": stream_body},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_agent_event_with_extra(
    now: int,
    model: str,
    agent: str,
    status: str,
    message: str,
    *,
    truncate_fn: Any = None,
    **extra: Any,
) -> str:
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
    return sse_delta_line(now, model, text)


def build_done(now: int, model: str) -> str:
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_error(now: int, model: str, message: str) -> str:
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
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": None}],
    }
    payload.update(extra_fields)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def ensure_task_dirs(task_dir: Path, agents_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)
