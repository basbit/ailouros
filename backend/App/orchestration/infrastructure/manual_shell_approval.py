
from __future__ import annotations

import threading
import time
from typing import Any, Optional

from backend.App.orchestration.infrastructure.approval_store import (
    clear_pending,
    clear_result,
    load_pending,
    load_result,
    store_pending,
    store_result,
)

_MANUAL_SHELL_EVENTS: dict[str, threading.Event] = {}

_DEFAULT_TIMEOUT_SEC = 900


def _timeout_sec() -> int:
    import os
    raw = (os.getenv("SWARM_MANUAL_SHELL_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SEC
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC
    return value if value > 0 else _DEFAULT_TIMEOUT_SEC


def _build_pending_payload(
    commands: list[str],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "commands": list(commands or []),
        "reason": (reason or "").strip(),
    }


def request_manual_execution(
    task_id: str,
    commands: list[str],
    task_store: Any,
    *,
    reason: str,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    if not commands:
        return False
    ev = threading.Event()
    _MANUAL_SHELL_EVENTS[task_id] = ev
    clear_result(task_id)
    store_pending(
        "manual-shell",
        task_id,
        _build_pending_payload(commands, reason=reason),
    )

    task_store.update_task(
        task_id,
        status="awaiting_manual_shell",
        agent="orchestrator",
        message=(
            f"Ожидание ручного выполнения {len(commands)} команд(ы): "
            f"{reason or 'система не может выполнить команду автоматически'}"
        ),
    )

    deadline = time.monotonic() + _timeout_sec()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _cleanup(task_id)
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if ev.wait(timeout=min(1.0, remaining)):
            break

    result = load_result(task_id)
    done = bool(result.get("approved")) if result else False
    _cleanup(task_id)
    return done


def pending_manual_payload(task_id: str) -> Optional[dict[str, Any]]:
    data = load_pending("manual-shell", task_id)
    if isinstance(data, dict):
        return {
            "commands": list(data.get("commands") or []),
            "reason": str(data.get("reason") or ""),
        }
    return None


def complete_manual_execution(task_id: str, done: bool) -> None:
    store_result(task_id, done)
    ev = _MANUAL_SHELL_EVENTS.get(task_id)
    if ev is not None:
        ev.set()


def _cleanup(task_id: str) -> None:
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
