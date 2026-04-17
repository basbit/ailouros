"""In-process human-gate approval (UI → POST confirm-human).

Pending data is stored in Redis (via approval_store) for persistence across restarts.
The pipeline thread blocks on a local threading.Event until the HTTP handler signals.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

_HUMAN_APPROVAL_EVENTS: dict[str, threading.Event] = {}

_HUMAN_APPROVAL_TIMEOUT_SEC = 3600


def request_human_approval(
    task_id: str,
    step: str,
    context: str,
    task_store: Any,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[bool, str]:
    """Block pipeline thread until approve/reject from UI (or timeout/cancel).

    Returns (approved, user_input_text).
    """
    ev = threading.Event()
    # Make the waiter/result path live before the UI can observe pending state.
    _HUMAN_APPROVAL_EVENTS[task_id] = ev
    clear_result(task_id)
    store_pending("human", task_id, context)

    task_store.update_task(
        task_id,
        status="awaiting_human",
        agent=step,
        message=f"Ожидание ручного подтверждения шага {step}",
    )

    deadline = time.monotonic() + _HUMAN_APPROVAL_TIMEOUT_SEC
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _cleanup(task_id)
            return False, ""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if ev.wait(timeout=min(1.0, remaining)):
            break

    result = load_result(task_id)
    approved = bool(result.get("approved")) if result else False
    user_input = str(result.get("user_input", "")) if result else ""
    _cleanup(task_id)
    return approved, user_input


def pending_human_context(task_id: str) -> Optional[str]:
    data = load_pending("human", task_id)
    if isinstance(data, str):
        return data
    return None


def complete_human_approval(task_id: str, approved: bool, user_input: str = "") -> None:
    """Called from HTTP route after user responds."""
    store_result(task_id, approved, user_input)
    ev = _HUMAN_APPROVAL_EVENTS.get(task_id)
    if ev is not None:
        ev.set()


def _cleanup(task_id: str) -> None:
    clear_pending("human", task_id)
    clear_result(task_id)
    _HUMAN_APPROVAL_EVENTS.pop(task_id, None)
