
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


def _build_pending_payload(
    commands: list[str],
    *,
    needs_allowlist: Optional[list[str]] = None,
    already_allowed: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "commands": list(commands or []),
        "needs_allowlist": list(dict.fromkeys(needs_allowlist or [])),  # de-dup, keep order
        "already_allowed": list(dict.fromkeys(already_allowed or [])),
    }


def request_shell_approval(
    task_id: str,
    commands: list[str],
    task_store: Any,
    *,
    cancel_event: Optional[threading.Event] = None,
    needs_allowlist: Optional[list[str]] = None,
    already_allowed: Optional[list[str]] = None,
) -> bool:
    if not commands:
        return False
    ev = threading.Event()
    _SHELL_APPROVAL_EVENTS[task_id] = ev
    clear_result(task_id)
    store_pending(
        "shell",
        task_id,
        _build_pending_payload(
            commands,
            needs_allowlist=needs_allowlist,
            already_allowed=already_allowed,
        ),
    )

    extra_hint = ""
    if needs_allowlist:
        extra_hint = f" (allowlist ext: {', '.join(dict.fromkeys(needs_allowlist))})"
    task_store.update_task(
        task_id,
        status="awaiting_shell_confirm",
        agent="orchestrator",
        message=f"Ожидание подтверждения {len(commands)} shell-команд{extra_hint}",
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
    if isinstance(data, dict):
        cmds = data.get("commands")
        if isinstance(cmds, list):
            return [str(c) for c in cmds]
        return None
    if isinstance(data, list):
        return data
    return None


def pending_shell_payload(task_id: str) -> Optional[dict[str, Any]]:
    data = load_pending("shell", task_id)
    if isinstance(data, dict):
        return {
            "commands": list(data.get("commands") or []),
            "needs_allowlist": list(data.get("needs_allowlist") or []),
            "already_allowed": list(data.get("already_allowed") or []),
        }
    if isinstance(data, list):
        return _build_pending_payload(data)
    return None


def complete_shell_approval(task_id: str, approved: bool) -> None:
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
