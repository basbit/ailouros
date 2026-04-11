"""Подтверждение выполнения shell-команд из <swarm_shell> (UI → POST confirm-shell).

Pending data is stored in Redis (via approval_store) for persistence across restarts.
The pipeline thread blocks on a local threading.Event until the HTTP handler signals.
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

_SHELL_APPROVAL_EVENTS: dict[str, threading.Event] = {}

_SHELL_APPROVAL_TIMEOUT_SEC = 300


def request_shell_approval(
    task_id: str,
    commands: list[str],
    task_store: Any,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    """Блокирует поток пайплайна до approve/reject в UI (или таймаута / cancel).

    Возвращает True только если пользователь явно подтвердил.
    """
    if not commands:
        return False
    ev = threading.Event()
    store_pending("shell", task_id, commands)
    _SHELL_APPROVAL_EVENTS[task_id] = ev
    clear_result(task_id)

    task_store.update_task(
        task_id,
        status="awaiting_shell_confirm",
        agent="orchestrator",
        message=f"Ожидание подтверждения {len(commands)} shell-команд",
    )

    deadline = time.monotonic() + _SHELL_APPROVAL_TIMEOUT_SEC
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
    approved = bool(result.get("approved")) if result else False
    _cleanup(task_id)
    return approved


def pending_shell_commands(task_id: str) -> Optional[list[str]]:
    data = load_pending("shell", task_id)
    if isinstance(data, list):
        return data
    return None


def complete_shell_approval(task_id: str, approved: bool) -> None:
    """Вызывается из HTTP-роута после ответа пользователя."""
    store_result(task_id, approved)
    ev = _SHELL_APPROVAL_EVENTS.get(task_id)
    if ev is not None:
        ev.set()


def _cleanup(task_id: str) -> None:
    clear_pending("shell", task_id)
    clear_result(task_id)
    _SHELL_APPROVAL_EVENTS.pop(task_id, None)


def run_shell_after_user_approval(
    task_id: str,
    snapshot: dict[str, Any],
    task_store: Any,
    *,
    cancel_event: Optional[threading.Event] = None,
    skip_all_shell: bool = False,
) -> bool:
    """True только после явного подтверждения в UI (или если shell отключён — False).

    ``skip_all_shell=True`` — финальный merge после стрима: инкремент уже выполнил команды.
    """
    from backend.App.workspace.infrastructure.workspace_io import command_exec_allowed
    from backend.App.workspace.infrastructure.patch_parser import (
        extract_shell_commands,
        merged_workspace_source_text,
    )

    if skip_all_shell or not command_exec_allowed():
        return False
    merged = merged_workspace_source_text(snapshot)
    cmds = extract_shell_commands(merged)
    if not cmds:
        return False
    return request_shell_approval(
        task_id, cmds, task_store, cancel_event=cancel_event
    )
