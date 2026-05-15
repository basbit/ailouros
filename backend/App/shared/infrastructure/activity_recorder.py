from __future__ import annotations

import contextvars
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.App.paths import artifacts_root

logger = logging.getLogger(__name__)

_TASK_CONTEXT: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "activity_recorder_task_id", default=None
)
_FILE_LOCKS_LOCK = threading.RLock()
_FILE_LOCKS: dict[Path, threading.Lock] = {}

_VALID_CHANNELS = frozenset(
    {"mcp_calls", "web_searches", "qdrant_ops", "rag_hits", "doc_ops"}
)
_MAX_PREVIEW_CHARS = 500


def set_active_task(task_id: Optional[str]) -> contextvars.Token:
    cleaned = task_id.strip() if isinstance(task_id, str) else None
    return _TASK_CONTEXT.set(cleaned or None)


def reset_active_task(token: contextvars.Token) -> None:
    _TASK_CONTEXT.reset(token)


def active_task() -> Optional[str]:
    return _TASK_CONTEXT.get()


def activity_dir_for(task_id: str) -> Path:
    if not task_id or not task_id.strip():
        raise ValueError("task_id must be non-empty")
    return (artifacts_root() / task_id.strip() / "activity").resolve()


def _channel_path(task_id: str, channel: str) -> Path:
    if channel not in _VALID_CHANNELS:
        raise ValueError(
            f"unknown activity channel {channel!r}; expected one of {sorted(_VALID_CHANNELS)}"
        )
    return activity_dir_for(task_id) / f"{channel}.jsonl"


def _lock_for(path: Path) -> threading.Lock:
    with _FILE_LOCKS_LOCK:
        existing = _FILE_LOCKS.get(path)
        if existing is None:
            existing = threading.Lock()
            _FILE_LOCKS[path] = existing
        return existing


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _truncate_for_preview(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _MAX_PREVIEW_CHARS:
            return value
        return value[:_MAX_PREVIEW_CHARS] + "…"
    if isinstance(value, dict):
        return {k: _truncate_for_preview(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_for_preview(item) for item in value]
    return value


def record(
    channel: str,
    payload: dict[str, Any],
    *,
    task_id: Optional[str] = None,
    step: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    target_task = task_id if task_id and task_id.strip() else active_task()
    if not target_task:
        return None
    entry: dict[str, Any] = {
        "ts": _utc_iso(),
        "channel": channel,
        "task_id": target_task,
    }
    if step and step.strip():
        entry["step"] = step.strip()
    entry.update(_truncate_for_preview(payload))
    path = _channel_path(target_task, channel)
    lock = _lock_for(path)
    serialized = json.dumps(entry, ensure_ascii=False) + "\n"
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
    return entry


def read_tail(
    task_id: str,
    channel: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    path = _channel_path(task_id, channel)
    if not path.is_file():
        return []
    capped = max(1, min(2000, limit))
    with _lock_for(path):
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    tail = raw_lines[-capped:]
    result: list[dict[str, Any]] = []
    for raw in tail:
        cleaned = raw.strip()
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            result.append(data)
    return result


def available_channels() -> tuple[str, ...]:
    return tuple(sorted(_VALID_CHANNELS))


__all__ = [
    "active_task",
    "activity_dir_for",
    "available_channels",
    "read_tail",
    "record",
    "reset_active_task",
    "set_active_task",
]
