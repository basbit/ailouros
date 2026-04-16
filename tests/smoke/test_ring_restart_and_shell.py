"""Smoke tests: ring topology wrap-around + shell-command approval flow.

These tests verify two behavioural contracts:

1. **Ring wrap-around** — when topology=ring is set with explicit pipeline_steps
   and the pipeline finishes with open defects, the pipeline restarts with defect
   context injected into user_input.  The restart inherits previous state and is
   capped by SWARM_RING_MAX_RESTARTS.

2. **Shell approval flow** — when an agent produces ``<swarm_shell>`` commands
   whose binary is NOT in the env allowlist, execution is gated until the user
   approves, and the per-task runtime allowlist is scoped (no cross-task leakage).

Tests use stubbed LLM responses (no Ollama / Anthropic required).
Gated behind ``SWARM_SMOKE=1`` to keep ``make ci`` fast.

Run::

    cd app
    SWARM_SMOKE=1 pytest tests/smoke/test_ring_restart_and_shell.py -v
"""

from __future__ import annotations

import contextlib
import os
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SMOKE_ENABLED = os.environ.get("SWARM_SMOKE", "").strip() == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason="Smoke tests require SWARM_SMOKE=1 (use `make smoke`).",
)


# ---------------------------------------------------------------------------
# 1. Ring wrap-around
# ---------------------------------------------------------------------------

class TestRingDefectContext:
    """Unit-level checks for _ring_defect_context helper."""

    def test_includes_all_defects(self):
        from backend.App.orchestration.application.pipeline_runners import _ring_defect_context

        defects = [
            {"severity": "P0", "description": "Null pointer", "location": "handler.py"},
            {"severity": "P1", "description": "Missing validation", "location": ""},
        ]
        ctx = _ring_defect_context(defects, pass_num=1)

        assert "Ring pass 1" in ctx
        assert "P0" in ctx
        assert "Null pointer" in ctx
        assert "handler.py" in ctx
        assert "P1" in ctx
        assert "Missing validation" in ctx
        assert "MUST be resolved" in ctx

    def test_pass_number_in_header(self):
        from backend.App.orchestration.application.pipeline_runners import _ring_defect_context

        ctx = _ring_defect_context([{"severity": "P0", "description": "Bug"}], pass_num=3)
        assert "Ring pass 3" in ctx

    def test_limits_to_ten_defects(self):
        from backend.App.orchestration.application.pipeline_runners import _ring_defect_context

        defects = [{"severity": "P1", "description": f"Bug {i}"} for i in range(15)]
        ctx = _ring_defect_context(defects, pass_num=1)

        # Only first 10 should appear (by description prefix "Bug ")
        shown = sum(1 for i in range(15) if f"Bug {i}" in ctx)
        assert shown <= 10, f"Expected ≤10 defects shown, found {shown}"

    def test_no_defects_produces_valid_block(self):
        from backend.App.orchestration.application.pipeline_runners import _ring_defect_context

        ctx = _ring_defect_context([], pass_num=2)
        assert "Ring pass 2" in ctx  # header still present


class TestRingRestartConditions:
    """Verify conditions under which ring_restart events are / are not emitted."""

    def _make_ring_restart_generator(self, monkeypatch, *, open_defects, topology, ring_max="1"):
        """Build a stubbed run_pipeline_stream generator with the given conditions.

        We patch all lazy imports at their source modules to satisfy the import
        resolution that happens inside run_pipeline_stream on first call.
        """
        from backend.App.orchestration.application.pipeline_runners import run_pipeline_stream

        state: dict[str, Any] = {
            "input": "Build something",
            "open_defects": list(open_defects),
            "_needs_work_count": len(open_defects),
            "agent_config": {},
        }
        monkeypatch.setenv("SWARM_RING_MAX_RESTARTS", ring_max)

        mock_exec = MagicMock()
        mock_exec.run = MagicMock(return_value=iter([]))
        mock_extr = MagicMock()
        mock_extr.emit_completed = MagicMock(return_value={"agent": "pm", "status": "completed", "message": ""})

        patches = [
            patch("backend.App.orchestration.application.pipeline_graph._initial_pipeline_state", return_value=state),
            patch("backend.App.orchestration.application.pipeline_graph.validate_pipeline_steps"),
            patch("backend.App.orchestration.application.pipeline_graph._resolve_pipeline_step", return_value=("running", lambda s: {})),
            patch("backend.App.orchestration.application.pipeline_graph._compact_state_if_needed", return_value=None),
            patch("backend.App.orchestration.application.pipeline_graph._pipeline_should_cancel", return_value=False),
            patch("backend.App.orchestration.application.pipeline_graph._state_snapshot", return_value={}),
            patch("backend.App.orchestration.application.pipeline_display.pipeline_step_in_progress_message", return_value="running"),
            patch("backend.App.orchestration.application.pipeline_runners.reset_pipeline_machine"),
            patch("backend.App.orchestration.application.pipeline_runners.get_pipeline_machine", return_value=MagicMock()),
            patch("backend.App.orchestration.application.pipeline_runners._sync_pipeline_machine"),
            patch("backend.App.orchestration.application.pipeline_runners._prepare_pipeline_machine_for_step"),
            patch("backend.App.orchestration.application.pipeline_runners._finalize_pipeline_machine"),
            patch("backend.App.orchestration.application.pipeline_runners._finalize_pipeline_metrics"),
            patch("backend.App.orchestration.application.pipeline_runners._run_post_step_enforcement", return_value=iter([])),
            patch("backend.App.orchestration.application.pipeline_runners._step_executor", mock_exec),
            patch("backend.App.orchestration.application.pipeline_runners._step_extractor", mock_extr),
        ]

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            agent_config: dict[str, Any] = {"swarm": {"topology": topology}}
            gen = run_pipeline_stream(
                "Build something",
                agent_config=agent_config,
                pipeline_steps=["pm"],
                task_id="test-ring-001",
            )
            events: list[dict] = []
            try:
                while True:
                    events.append(next(gen))
            except StopIteration:
                pass

        return events

    def test_ring_restart_emitted_when_defects_remain(self, monkeypatch):
        events = self._make_ring_restart_generator(
            monkeypatch,
            open_defects=[{"severity": "P1", "description": "Missing error handling", "location": "api.py"}],
            topology="ring",
            ring_max="1",
        )
        ring_events = [e for e in events if e.get("status") == "ring_restart"]
        assert ring_events, (
            "Expected ring_restart event when open_defects remain + ring topology. "
            f"Event statuses: {[e.get('status') for e in events]}"
        )
        assert ring_events[0]["restart_pass"] == 1
        assert ring_events[0]["defect_count"] == 1

    def test_ring_restart_does_not_fire_without_ring_topology(self, monkeypatch):
        events = self._make_ring_restart_generator(
            monkeypatch,
            open_defects=[{"severity": "P1", "description": "Bug"}],
            topology="linear",
            ring_max="1",
        )
        ring_events = [e for e in events if e.get("status") == "ring_restart"]
        assert not ring_events, f"Unexpected ring_restart for linear topology: {ring_events}"

    def test_ring_restart_respects_max_restarts_zero(self, monkeypatch):
        events = self._make_ring_restart_generator(
            monkeypatch,
            open_defects=[{"severity": "P0", "description": "Critical bug"}],
            topology="ring",
            ring_max="0",
        )
        ring_events = [e for e in events if e.get("status") == "ring_restart"]
        assert not ring_events, f"Unexpected ring_restart when SWARM_RING_MAX_RESTARTS=0: {ring_events}"

    def test_ring_restart_no_fire_when_no_defects(self, monkeypatch):
        events = self._make_ring_restart_generator(
            monkeypatch,
            open_defects=[],
            topology="ring",
            ring_max="1",
        )
        ring_events = [e for e in events if e.get("status") == "ring_restart"]
        assert not ring_events, f"Unexpected ring_restart when no defects: {ring_events}"


# ---------------------------------------------------------------------------
# 2. Shell approval flow
# ---------------------------------------------------------------------------

class TestShellApprovalFlow:
    """Shell commands outside the allowlist trigger an approval request."""

    def test_out_of_allowlist_command_binary_extracted(self):
        """extract_command_binary correctly identifies the binary of a shell command."""
        from backend.App.workspace.infrastructure.workspace_io import extract_command_binary

        assert extract_command_binary("godot --headless project.tscn") == "godot"
        assert extract_command_binary("npm install") == "npm"
        assert extract_command_binary("python scripts/build.py --clean") == "python"
        assert extract_command_binary("") is None
        assert extract_command_binary("  ") is None

    def test_out_of_allowlist_command_is_blocked(self, monkeypatch):
        """A binary not in SWARM_SHELL_ALLOWLIST returns (False, reason) from _shell_command_allowed."""
        from backend.App.workspace.infrastructure.workspace_io import (
            _shell_command_allowed,
            _shell_allowlist,
            scoped_runtime_shell_allowlist,
        )

        monkeypatch.setenv("SWARM_SHELL_ALLOWLIST", "npm,node,python,pytest")
        monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "1")

        allowlist = _shell_allowlist()
        assert "godot" not in allowlist, f"Test assumes godot NOT in allowlist, got {allowlist}"

        with scoped_runtime_shell_allowlist():
            allowed, reason = _shell_command_allowed("godot --headless project.tscn")
            assert not allowed, f"godot should be blocked before approval, reason: {reason}"
            assert "godot" in reason

    def test_allowed_command_passes_check(self, monkeypatch):
        """A binary in SWARM_SHELL_ALLOWLIST returns (True, '') from _shell_command_allowed."""
        from backend.App.workspace.infrastructure.workspace_io import (
            _shell_command_allowed,
            scoped_runtime_shell_allowlist,
        )

        monkeypatch.setenv("SWARM_SHELL_ALLOWLIST", "npm,node,python,pytest")
        monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "1")

        with scoped_runtime_shell_allowlist():
            allowed, reason = _shell_command_allowed("npm install")
            assert allowed, f"npm should be allowed, reason: {reason}"

    def test_runtime_allowlist_extension_permits_subsequent_run(self, monkeypatch):
        """After user approves, extend_runtime_shell_allowlist lets the binary through."""
        from backend.App.workspace.infrastructure.workspace_io import (
            extend_runtime_shell_allowlist,
            scoped_runtime_shell_allowlist,
            _shell_command_allowed,
        )

        monkeypatch.setenv("SWARM_SHELL_ALLOWLIST", "npm,node,python")
        monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "1")

        with scoped_runtime_shell_allowlist():
            # Before approval: godot is blocked
            allowed_before, _ = _shell_command_allowed("godot --headless project.tscn")
            assert not allowed_before, "godot must be blocked before user approval"

            # Simulate user approval
            extend_runtime_shell_allowlist(["godot"])

            # After approval: godot is allowed
            allowed_after, _ = _shell_command_allowed("godot --headless project.tscn")
            assert allowed_after, "godot must be allowed after extend_runtime_shell_allowlist"

        # After scope exits: godot is blocked again (per-task isolation)
        with scoped_runtime_shell_allowlist():
            allowed_new_scope, _ = _shell_command_allowed("godot --headless project.tscn")
            assert not allowed_new_scope, (
                "godot must be blocked again in a new task scope (no allowlist leak)"
            )

    def test_runtime_allowlist_does_not_leak_across_tasks(self, monkeypatch):
        """The per-task allowlist extension is isolated — a concurrent task starts clean."""
        from backend.App.workspace.infrastructure.workspace_io import (
            extend_runtime_shell_allowlist,
            scoped_runtime_shell_allowlist,
            _shell_command_allowed,
        )

        monkeypatch.setenv("SWARM_SHELL_ALLOWLIST", "npm")
        monkeypatch.setenv("SWARM_ALLOW_COMMAND_EXEC", "1")

        results: dict[str, bool] = {"task1": False, "task2": False}

        def task1() -> None:
            with scoped_runtime_shell_allowlist():
                extend_runtime_shell_allowlist(["godot"])
                ok, _ = _shell_command_allowed("godot --headless x.tscn")
                results["task1"] = ok

        def task2() -> None:
            with scoped_runtime_shell_allowlist():
                ok, _ = _shell_command_allowed("godot --headless x.tscn")
                results["task2"] = ok

        t1 = threading.Thread(target=task1)
        t2 = threading.Thread(target=task2)
        t1.start()
        t1.join()
        t2.start()
        t2.join()

        assert results["task1"], "Task 1 should have allowed godot after extension"
        assert not results["task2"], (
            "Task 2 must NOT see task 1's allowlist extension (isolation violation)"
        )
