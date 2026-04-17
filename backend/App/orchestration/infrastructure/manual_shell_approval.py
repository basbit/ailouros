"""Dialog for commands the orchestrator cannot execute itself.

Some commands the agent asks to run are structurally unsupported by the
automated shell:

* ``sudo …`` — needs a TTY / password prompt, no stdin, would hang.
* Commands that require interactive input (any tool with a ``y/N`` prompt).
* Commands the user explicitly rejected once but which the agent may re-propose.

Rather than silently dropping them (which leaves the agent repeating the same
mistake) or blocking on a password prompt (which froze the entire pipeline in
task ``273b3bf1`` for 5 minutes), we surface a dedicated UI dialog:

    "I can't run this command — please run it yourself:  [command]
        [Done]  [Cancel]"

* **Done**   — user ran the command in their own terminal; pipeline continues
               as if it executed successfully. Agent is told to assume the
               side-effects are in place.
* **Cancel** — user declined; agent must find an alternative on retry.

The backend blocks the pipeline thread on a local ``threading.Event`` exactly
like ``shell_approval``; pending state lives in the same Redis-backed store
under a dedicated ``manual-shell`` gate type so the two flows don't collide.
"""

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

# Manual execution often means the user has to open a terminal, type sudo
# password, wait for the command — 15 minutes is generous without being
# unbounded. Override with SWARM_MANUAL_SHELL_TIMEOUT_SEC.
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
    """Schema stored in the approval store for the UI.

    ``commands`` — exact command lines the agent asked to run, verbatim.
    ``reason``   — human-readable explanation why the orchestrator can't run
                    them itself (e.g. ``"sudo is not supported by the
                    automated shell"``). Displayed above the command list.
    """
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
    """Ask the user to run *commands* manually; block until Done or Cancel.

    Returns True only when the user explicitly clicked "Done". Returns False on
    cancel, timeout, or cancel_event.
    """
    if not commands:
        return False
    ev = threading.Event()
    # Register the waiter and clear stale result *before* exposing pending data.
    # Otherwise a fast UI poller can submit Done/Cancel in the tiny window where
    # pending exists but the event/result channel is not ready yet.
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
    # The same result key is shared with shell_approval / human_approval —
    # we re-use it because the HTTP handler writes to store_result(), but we
    # interpret ``approved`` as "done=true". Cancel maps to approved=false.
    done = bool(result.get("approved")) if result else False
    _cleanup(task_id)
    return done


def pending_manual_payload(task_id: str) -> Optional[dict[str, Any]]:
    """Return the pending dialog payload, or None if none is pending."""
    data = load_pending("manual-shell", task_id)
    if isinstance(data, dict):
        return {
            "commands": list(data.get("commands") or []),
            "reason": str(data.get("reason") or ""),
        }
    return None


def complete_manual_execution(task_id: str, done: bool) -> None:
    """Called by the HTTP handler after the user clicked Done or Cancel."""
    store_result(task_id, done)
    ev = _MANUAL_SHELL_EVENTS.get(task_id)
    if ev is not None:
        ev.set()


def _cleanup(task_id: str) -> None:
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
