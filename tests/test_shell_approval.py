"""Tests for backend/App/orchestration/infrastructure/shell_approval.py."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch


from backend.App.orchestration.infrastructure.shell_approval import (
    _SHELL_APPROVAL_EVENTS,
    complete_shell_approval,
    pending_shell_commands,
    request_shell_approval,
    run_shell_after_user_approval,
)
from backend.App.orchestration.infrastructure.approval_store import (
    clear_pending,
    store_pending,
)


def _make_task_store():
    ts = MagicMock()
    ts.update_task.return_value = None
    return ts


# ---------------------------------------------------------------------------
# pending_shell_commands
# ---------------------------------------------------------------------------

def test_pending_shell_commands_none():
    clear_pending("shell", "t99")
    assert pending_shell_commands("t99") is None


def test_pending_shell_commands_present():
    store_pending("shell", "t-pend", ["cmd1", "cmd2"])
    assert pending_shell_commands("t-pend") == ["cmd1", "cmd2"]
    clear_pending("shell", "t-pend")


# ---------------------------------------------------------------------------
# complete_shell_approval
# ---------------------------------------------------------------------------

def test_complete_shell_approval_approved():
    ev = threading.Event()
    _SHELL_APPROVAL_EVENTS["t-approve"] = ev
    complete_shell_approval("t-approve", approved=True)
    assert ev.is_set()
    _SHELL_APPROVAL_EVENTS.pop("t-approve", None)


def test_complete_shell_approval_rejected():
    ev = threading.Event()
    _SHELL_APPROVAL_EVENTS["t-reject"] = ev
    complete_shell_approval("t-reject", approved=False)
    assert ev.is_set()
    _SHELL_APPROVAL_EVENTS.pop("t-reject", None)


def test_complete_shell_approval_no_event():
    _SHELL_APPROVAL_EVENTS.pop("t-no-ev", None)
    complete_shell_approval("t-no-ev", approved=True)
    # Should not raise


# ---------------------------------------------------------------------------
# request_shell_approval
# ---------------------------------------------------------------------------

def test_request_shell_approval_empty_commands():
    result = request_shell_approval("t-empty", [], _make_task_store())
    assert result is False


def test_request_shell_approval_approved_immediately():
    ts = _make_task_store()
    task_id = "t-immediate-approve"

    def approve_later():
        time.sleep(0.05)
        complete_shell_approval(task_id, approved=True)

    t = threading.Thread(target=approve_later, daemon=True)
    t.start()
    result = request_shell_approval(task_id, ["cmd1"], ts)
    t.join(timeout=2)
    assert result is True


def test_request_shell_approval_rejected():
    ts = _make_task_store()
    task_id = "t-reject-shell"

    def reject_later():
        time.sleep(0.05)
        complete_shell_approval(task_id, approved=False)

    t = threading.Thread(target=reject_later, daemon=True)
    t.start()
    result = request_shell_approval(task_id, ["cmd1"], ts)
    t.join(timeout=2)
    assert result is False


def test_request_shell_approval_cancel_event():
    ts = _make_task_store()
    task_id = "t-cancel-shell"
    cancel = threading.Event()

    def cancel_later():
        time.sleep(0.05)
        cancel.set()

    t = threading.Thread(target=cancel_later, daemon=True)
    t.start()
    result = request_shell_approval(task_id, ["cmd1"], ts, cancel_event=cancel)
    t.join(timeout=2)
    assert result is False
    # Cleanup should have happened
    assert pending_shell_commands(task_id) is None


# ---------------------------------------------------------------------------
# run_shell_after_user_approval
# ---------------------------------------------------------------------------

def test_run_shell_after_user_approval_skip_all():
    result = run_shell_after_user_approval(
        "t-skip", {}, _make_task_store(), skip_all_shell=True
    )
    assert result is False


def test_run_shell_after_user_approval_command_exec_disabled():
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=False,
    ):
        result = run_shell_after_user_approval("t-no-exec", {}, _make_task_store())
    assert result is False


def test_run_shell_after_user_approval_no_commands():
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=True,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.merged_workspace_source_text",
        return_value="no shell tags here",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.extract_shell_commands",
        return_value=[],
    ):
        result = run_shell_after_user_approval("t-no-cmds", {}, _make_task_store())
    assert result is False


def test_run_shell_after_user_approval_with_commands_approved():
    task_id = "t-with-cmds-approved"
    ts = _make_task_store()

    def approve_later():
        time.sleep(0.05)
        complete_shell_approval(task_id, approved=True)

    with patch(
        "backend.App.workspace.infrastructure.workspace_io.command_exec_allowed",
        return_value=True,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.merged_workspace_source_text",
        return_value='<swarm_shell>echo hi</swarm_shell>',
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.extract_shell_commands",
        return_value=["echo hi"],
    ):
        t = threading.Thread(target=approve_later, daemon=True)
        t.start()
        result = run_shell_after_user_approval(task_id, {}, ts)
        t.join(timeout=2)
    assert result is True
