from __future__ import annotations

from typing import Any, Optional


def shell_pending_payload(task_id: str) -> Optional[dict[str, Any]]:
    from backend.App.orchestration.infrastructure.shell_approval import pending_shell_payload
    return pending_shell_payload(task_id)


def shell_pending_commands(task_id: str) -> Optional[list[str]]:
    from backend.App.orchestration.infrastructure.shell_approval import pending_shell_commands
    return pending_shell_commands(task_id)


def shell_complete_approval(task_id: str, approved: bool) -> None:
    from backend.App.orchestration.infrastructure.shell_approval import complete_shell_approval
    complete_shell_approval(task_id, approved)


def manual_shell_pending_payload(task_id: str) -> Optional[dict[str, Any]]:
    from backend.App.orchestration.infrastructure.manual_shell_approval import pending_manual_payload
    return pending_manual_payload(task_id)


def manual_shell_complete(task_id: str, done: bool) -> None:
    from backend.App.orchestration.infrastructure.manual_shell_approval import complete_manual_execution
    complete_manual_execution(task_id, done)


def human_pending_payload(task_id: str) -> dict[str, Any]:
    from backend.App.orchestration.infrastructure.human_approval import pending_human_payload
    return pending_human_payload(task_id)


def human_pending_context(task_id: str) -> Optional[str]:
    from backend.App.orchestration.infrastructure.human_approval import pending_human_context
    return pending_human_context(task_id)


def human_complete_approval(task_id: str, approved: bool, user_input: str = "") -> None:
    from backend.App.orchestration.infrastructure.human_approval import complete_human_approval
    complete_human_approval(task_id, approved, user_input)
