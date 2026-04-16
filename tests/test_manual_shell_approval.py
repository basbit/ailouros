"""Tests for backend/App/orchestration/infrastructure/manual_shell_approval.py.

Covers the manual-execution flow used when the orchestrator cannot run a
command itself (sudo with no TTY, interactive prompts). User runs the command
in their own terminal and clicks Done / Cancel.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock


from backend.App.orchestration.infrastructure.approval_store import (
    clear_pending,
    clear_result,
)
from backend.App.orchestration.infrastructure.manual_shell_approval import (
    _MANUAL_SHELL_EVENTS,
    complete_manual_execution,
    pending_manual_payload,
    request_manual_execution,
)


def _make_task_store() -> MagicMock:
    ts = MagicMock()
    ts.update_task.return_value = None
    return ts


# ---------------------------------------------------------------------------
# pending_manual_payload
# ---------------------------------------------------------------------------

def test_pending_manual_payload_none():
    clear_pending("manual-shell", "t-none")
    assert pending_manual_payload("t-none") is None


def test_pending_manual_payload_present_after_request_thread():
    """After request_manual_execution starts, payload is visible to UI poller."""
    task_id = "t-manual-1"
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    ts = _make_task_store()

    def _run() -> None:
        request_manual_execution(
            task_id, ["sudo apt-get install x"], ts, reason="no TTY",
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    # Give the worker a moment to register pending.
    for _ in range(30):
        if pending_manual_payload(task_id) is not None:
            break
        time.sleep(0.05)

    payload = pending_manual_payload(task_id)
    assert payload is not None
    assert payload["commands"] == ["sudo apt-get install x"]
    assert payload["reason"] == "no TTY"

    # Clean up by completing with Cancel so the worker exits.
    complete_manual_execution(task_id, False)
    worker.join(timeout=2.0)
    assert not worker.is_alive()


# ---------------------------------------------------------------------------
# complete_manual_execution ↔ request_manual_execution
# ---------------------------------------------------------------------------

def test_request_returns_true_on_done():
    task_id = "t-manual-done"
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    ts = _make_task_store()
    result_holder: list[bool] = []

    def _run() -> None:
        result_holder.append(
            request_manual_execution(task_id, ["sudo x"], ts, reason="r"),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    for _ in range(30):
        if pending_manual_payload(task_id) is not None:
            break
        time.sleep(0.05)

    complete_manual_execution(task_id, True)
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert result_holder == [True]
    # Post-completion the pending data is cleared so the UI stops polling.
    assert pending_manual_payload(task_id) is None


def test_request_returns_false_on_cancel():
    task_id = "t-manual-cancel"
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    ts = _make_task_store()
    result_holder: list[bool] = []

    def _run() -> None:
        result_holder.append(
            request_manual_execution(task_id, ["sudo y"], ts, reason="r"),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    for _ in range(30):
        if pending_manual_payload(task_id) is not None:
            break
        time.sleep(0.05)

    complete_manual_execution(task_id, False)
    worker.join(timeout=2.0)
    assert result_holder == [False]


def test_request_returns_false_on_cancel_event():
    """External cancel_event aborts the wait without user response."""
    task_id = "t-manual-cancel-event"
    _MANUAL_SHELL_EVENTS.pop(task_id, None)
    clear_pending("manual-shell", task_id)
    clear_result(task_id)
    ts = _make_task_store()
    cancel = threading.Event()
    result_holder: list[bool] = []

    def _run() -> None:
        result_holder.append(
            request_manual_execution(
                task_id, ["sudo z"], ts, reason="r", cancel_event=cancel,
            ),
        )

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    for _ in range(30):
        if pending_manual_payload(task_id) is not None:
            break
        time.sleep(0.05)

    cancel.set()
    worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert result_holder == [False]


def test_request_with_empty_commands_returns_false():
    """Empty command list → no UI prompt, immediate False."""
    ts = _make_task_store()
    assert request_manual_execution("t-empty", [], ts, reason="whatever") is False


# ---------------------------------------------------------------------------
# Interaction with sudo detection in _shell_command_allowed (contract)
# ---------------------------------------------------------------------------

def test_shell_command_allowed_rejects_sudo():
    """The automated shell must refuse sudo up-front — no hanging on /dev/tty."""
    from backend.App.workspace.infrastructure.workspace_io import (
        _shell_command_allowed,
    )
    ok, reason = _shell_command_allowed("sudo apt-get install curl")
    assert ok is False
    assert "sudo" in reason.lower()
    assert "not supported" in reason.lower()
