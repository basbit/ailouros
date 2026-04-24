"""Tests for backend/App/orchestration/application/pipeline_runners.py."""
import json
from typing import Any
from unittest.mock import patch

import pytest

from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled
from backend.App.orchestration.domain.defect import Defect, DefectReport, Severity
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase
from backend.App.orchestration.domain.gates import GateResult
from backend.App.orchestration.application.pipeline.pipeline_runners import (
    _enter_fix_cycle_or_escalate,
    _record_open_defects,
    run_pipeline_stream,
    run_pipeline_stream_resume,
    run_pipeline_stream_retry,
    run_pipeline_stream_staged,
)
from backend.UI.REST.schemas import validate_pipeline_stages


def _make_step_func(output_key=None, output_val="result"):
    """Return a step function that adds output_key to state."""
    def step_func(state):
        if output_key:
            state[output_key] = output_val
        return state
    return step_func


def _mock_pipeline_graph_symbols(step_ids, step_funcs=None):
    """Context manager providing all pipeline_graph symbols needed by runners."""
    if step_funcs is None:
        step_funcs = {sid: _make_step_func() for sid in step_ids}

    def fake_resolve_step(step_id, agent_config):
        return f"Running {step_id}", step_funcs.get(step_id, _make_step_func())

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        step_func(state)
        yield {"agent": step_id, "status": "progress", "message": "working"}

    def fake_emit_completed(step_id, state):
        return {"agent": step_id, "status": "completed", "message": "done"}

    patches = {
        "DEFAULT_PIPELINE_STEP_IDS": step_ids,
        "_compact_state_if_needed": lambda state, step_id: None,
        "_initial_pipeline_state": lambda *args, **kwargs: {
            "input": kwargs.get("user_input") or args[0] if args else "",
            "agent_config": args[1] if len(args) > 1 else {},
        },
        "_pipeline_should_cancel": lambda state: False,
        "_resolve_pipeline_step": fake_resolve_step,
        "_state_snapshot": lambda state: dict(state),
        "pipeline_step_in_progress_message": lambda step_id, state: f"Running {step_id}",
        "validate_pipeline_steps": lambda steps, ac: None,
    }
    return patches


def _drain_generator_with_return(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


# ---------------------------------------------------------------------------
# run_pipeline_stream
# ---------------------------------------------------------------------------

def test_run_pipeline_stream_yields_events():
    step_ids = ["pm", "ba"]
    _mock_pipeline_graph_symbols(step_ids)

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ):
        pass

    # Direct approach — call the real function with full mocked graph
    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ):
        gen = run_pipeline_stream(
            "user input",
            agent_config={},
            pipeline_steps=step_ids,
        )
        events = list(gen)
    # Should have in_progress + completed for each step
    in_progress = [e for e in events if e.get("status") == "in_progress"]
    completed = [e for e in events if e.get("status") == "completed"]
    assert len(in_progress) == 2
    assert len(completed) == 2


def test_run_pipeline_stream_returns_pipeline_metrics():
    step_ids = ["pm"]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}, "task_id": "task-123"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func("pm_output", "pm result")),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([fn(st)]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.integrations.infrastructure.observability.step_metrics.snapshot_for_task",
        return_value={"steps": {"pm": {"count": 1, "p50_ms": 10.0, "max_ms": 10.0, "tokens": {}}}, "role_model_top": [], "updated_at": 0},
    ):
        _, final_state = _drain_generator_with_return(
            run_pipeline_stream("user input", agent_config={}, pipeline_steps=step_ids)
        )

    assert final_state["pipeline_metrics"]["task_id"] == "task-123"
    assert "step_metrics" in final_state["pipeline_metrics"]
    assert "pm" in final_state["pipeline_metrics"]["step_metrics"]["steps"]


def test_run_pipeline_stream_pipeline_cancelled():
    step_ids = ["pm"]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=True,
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(PipelineCancelled):
            list(gen)


def test_run_pipeline_stream_step_exception_attaches_state():
    step_ids = ["pm"]

    def raising_run_step(sid, fn, state):
        raise RuntimeError("step failed")
        yield  # make it a generator

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=raising_run_step,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        return_value={"input": "test"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(RuntimeError) as exc_info:
            list(gen)
    assert hasattr(exc_info.value, "_partial_state")
    assert hasattr(exc_info.value, "_failed_step")
    assert exc_info.value._failed_step == "pm"


def test_run_pipeline_stream_human_approval_required():
    step_ids = ["pm"]
    exc = HumanApprovalRequired("pm", "needs approval")

    def raising_run_step(sid, fn, state):
        raise exc
        yield

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=raising_run_step,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        return_value={"input": "test"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(HumanApprovalRequired) as exc_info:
            list(gen)
    assert exc_info.value.resume_pipeline_step == "pm"


def test_run_pipeline_stream_blocks_on_review_pm_needs_work() -> None:
    step_ids = ["review_pm", "human_pm", "architect"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        # Review output must exceed _MIN_REVIEW_CONTENT_CHARS (120) to
        # take the real NEEDS_WORK path (shorter → empty-review escalation).
        state["pm_review_output"] = (
            "### Summary of Work\n"
            "PM built a plan with hardcoded stack choices.\n\n"
            "### Risks & Gaps\n"
            "Role separation violation: PM should not hardcode the technology stack. "
            "Architect is the source of truth for stack decisions.\n\n"
            "VERDICT: NEEDS_WORK"
        )
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed", "message": "done"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(HumanApprovalRequired) as exc_info:
            list(gen)

    assert exc_info.value.step == "review_pm"
    assert exc_info.value.resume_pipeline_step == "human_pm"


def test_run_pipeline_stream_auto_retries_review_pm_before_human_gate(monkeypatch) -> None:
    step_ids = ["review_pm"]
    calls = {"pm": 0, "review_pm": 0}

    def fake_resolve_step(step_id, agent_config):
        return ("Running", _make_step_func())

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        calls[step_id] = calls.get(step_id, 0) + 1
        if step_id == "pm":
            state["pm_output"] = f"pm attempt {calls['pm']}"
        if step_id == "review_pm":
            # Review output must exceed _MIN_REVIEW_CONTENT_CHARS (120) —
            # shorter outputs are treated as "empty review" and escalate
            # without retry (bug aec02899 guard). Use realistic-length
            # fixtures so we exercise the actual retry path.
            if calls["review_pm"] == 1:
                state["pm_review_output"] = (
                    "### Summary of Work\n"
                    "PM decomposed the task into 5 core activities with acceptance "
                    "criteria covering monetization, core loop, and persistence.\n\n"
                    "### Risks & Gaps\n"
                    "Role separation violation: PM hard-coded SQLite and AdMob — "
                    "the Architect should own stack decisions.\n\n"
                    "VERDICT: NEEDS_WORK"
                )
            else:
                state["pm_review_output"] = (
                    "### Summary of Work\n"
                    "PM decomposed the task clearly and removed the stack hardcoding "
                    "flagged in the previous round. Acceptance criteria are measurable.\n\n"
                    "VERDICT: APPROVED"
                )
        yield {"agent": step_id, "status": "progress", "message": "working"}

    monkeypatch.setenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "1")
    monkeypatch.setenv("SWARM_MAX_STEP_RETRIES", "1")

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}, "step_retries": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=fake_resolve_step,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed", "message": "done"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        events, final_state = _drain_generator_with_return(
            run_pipeline_stream("input", pipeline_steps=step_ids)
        )

    assert calls["pm"] == 1
    assert calls["review_pm"] == 2
    assert final_state["step_retries"]["pm"] == 1
    assert final_state["planning_review_blockers"] == []


def test_run_pipeline_stream_blocks_on_review_stack_needs_work() -> None:
    step_ids = ["review_stack", "human_arch", "review_arch"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["stack_review_output"] = "VERDICT: NEEDS_WORK\nStack claims are not evidence-backed."
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed", "message": "done"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(HumanApprovalRequired) as exc_info:
            list(gen)

    assert exc_info.value.step == "review_stack"
    assert exc_info.value.resume_pipeline_step == "human_arch"


def test_run_pipeline_stream_runs_verification_layer_after_dev(tmp_path):
    step_ids = ["dev"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
            "workspace_apply_writes": True,
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        events = list(run_pipeline_stream("input", pipeline_steps=step_ids))

    assert any(e.get("agent") == "verification_layer" for e in events)


def test_run_pipeline_stream_builds_verification_contract_from_deliverables(tmp_path):
    step_ids = ["dev"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
            "workspace_apply_writes": True,
            "deliverables_artifact": {
                "verification_commands": [{"command": "build_gate", "expected": "build gate passes"}],
            },
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[
            GateResult(True, "build_gate"),
            GateResult(True, "spec_gate"),
            GateResult(True, "consistency_gate"),
            GateResult(True, "stub_gate"),
            GateResult(True, "diff_risk_gate"),
        ],
    ):
        events, final_state = _drain_generator_with_return(
            run_pipeline_stream("input", pipeline_steps=step_ids)
        )

    assert any(e.get("agent") == "verification_layer" for e in events)
    assert final_state["verification_contract"]["expected_trusted_commands"] == [
        {"command": "build_gate", "expected": "build gate passes"}
    ]
    assert "build_gate" in final_state["verification_contract"]["gates_run"]
    assert final_state["dev_manifest"]["trusted_verification_commands"] == [
        {"command": "build_gate", "expected": "build gate passes"}
    ]


def test_run_pipeline_stream_blocks_write_errors_without_review_dev(tmp_path):
    step_ids = ["dev"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_patch path="app.py">broken</swarm_patch>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
            "workspace_apply_writes": True,
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "parsed": 0,
            "errors": ["patch 'app.py': hunk 1 failed"],
        },
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        with pytest.raises(RuntimeError, match="write_integrity_gate failed"):
            list(run_pipeline_stream("input", pipeline_steps=step_ids))


def test_run_pipeline_stream_blocks_devops_without_command_evidence(tmp_path):
    step_ids = ["devops", "review_devops"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        if step_id == "devops":
            state["devops_output"] = "High level CI/CD prose only."
        elif step_id == "review_devops":
            state["devops_review_output"] = "VERDICT: OK"
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
            "workspace_apply_writes": True,
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        with pytest.raises(RuntimeError, match="DevOps did not provide executable"):
            list(run_pipeline_stream("input", pipeline_steps=step_ids))


def test_run_pipeline_stream_blocks_empty_required_role_output(tmp_path):
    step_ids = ["ux_researcher"]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ):
        with pytest.raises(RuntimeError, match="ux_researcher: required output ux_researcher_output is empty"):
            list(run_pipeline_stream("input", pipeline_steps=step_ids))


def test_run_pipeline_stream_fails_on_unjustified_full_file_rewrite(tmp_path):
    step_ids = ["dev"]
    # Shared state dict — mutated by the pipeline in-place so we can inspect it after.
    shared_state: dict = {
        "input": "test",
        "agent_config": {},
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_file path="app.py">print("rewritten")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value=shared_state,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
        ), patch(
            "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
            return_value={
                "written": [],
                "patched": [],
                "udiff_applied": [],
                "write_actions": [{"path": "app.py", "mode": "overwrite_file"}],
                "parsed": 1,
            },
    ):
        # Gate failure is now reported as a warning (not a raised error) so QA can report on it.
        list(run_pipeline_stream("input", pipeline_steps=step_ids))

    assert "FULL_FILE_REWRITE_REQUIRES_JUSTIFICATION" in shared_state.get(
        "verification_gate_warnings", ""
    )


def test_run_pipeline_stream_fails_on_unjustified_mcp_full_file_rewrite(tmp_path):
    step_ids = ["dev"]
    shared_state: dict = {
        "input": "test",
        "agent_config": {},
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = "Implemented via MCP write tool"
        state["dev_mcp_write_count"] = 1
        state["dev_mcp_write_actions"] = [{"path": "app.py", "mode": "overwrite_file"}]
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value=shared_state,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={
            "written": [],
            "patched": [],
            "udiff_applied": [],
            "write_actions": [],
            "parsed": 0,
        },
    ):
        # Gate failure is now reported as a warning (not a raised error) so QA can report on it.
        list(run_pipeline_stream("input", pipeline_steps=step_ids))

    assert "FULL_FILE_REWRITE_REQUIRES_JUSTIFICATION" in shared_state.get(
        "verification_gate_warnings", ""
    )


def test_run_pipeline_stream_rejects_manifest_mismatch_with_deliverables(tmp_path):
    step_ids = ["dev"]
    shared_state: dict = {
        "input": "test",
        "agent_config": {},
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
        "deliverables_artifact": {
            "verification_commands": [{"command": "build_gate", "expected": "build gate passes"}],
        },
    }

    manifest = {
        "changed_files": ["app.py"],
        "trusted_verification_commands": [{"command": "spec_gate", "expected": "spec gate passes"}],
    }

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = (
            '<dev_manifest>'
            + json.dumps(manifest)
            + '</dev_manifest>\n'
            + '<swarm_file path="app.py">print("ok")</swarm_file>'
        )
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value=shared_state,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ):
        # Gate failure is now reported as a warning (not a raised error) so QA can report on it.
        list(run_pipeline_stream("input", pipeline_steps=step_ids))

    # Manifest/deliverables mismatch is now a warning (not a raised error):
    # the pipeline uses the deliverables version and continues to run gates.
    # Verify: the verification_contract was recorded, showing the mismatch was resolved.
    contract = shared_state.get("verification_contract", {})
    assert "expected_trusted_commands" in contract, (
        "verification_contract should be written when deliverables mismatch occurs"
    )
    # The gates still fail (empty workspace) so verification_gate_warnings is set.
    assert shared_state.get("verification_gate_warnings"), (
        "gate failures should be recorded in verification_gate_warnings"
    )


def test_record_open_defects_builds_clustered_open_defects():
    state = {}
    report = DefectReport(
        defects=[
            Defect(id="d1", title="Missing file 1", severity=Severity.P1, category="missing_file", file_paths=["a.py"]),
            Defect(id="d2", title="Missing file 2", severity=Severity.P1, category="missing_file", file_paths=["b.py"]),
            Defect(id="d3", title="Regression", severity=Severity.P0, category="regression", file_paths=["c.py"]),
        ]
    )

    _record_open_defects(state, report)  # type: ignore[arg-type]

    assert len(state["open_defects"]) == 3
    assert len(state["clustered_open_defects"]) == 2
    missing_cluster = next(c for c in state["clustered_open_defects"] if c["cluster_key"] == "missing_file")
    assert missing_cluster["count"] == 2
    assert missing_cluster["file_paths"] == ["a.py", "b.py"]


def test_enter_fix_cycle_or_escalate_counts_duplicate_category_once():
    state = {"open_defects": [], "clustered_open_defects": []}
    machine = PipelineMachine()
    machine.transition(PipelinePhase.IMPLEMENT)
    machine.transition(PipelinePhase.VERIFY)
    report = DefectReport(
        defects=[
            Defect(id="d1", title="A", severity=Severity.P1, category="missing_file"),
            Defect(id="d2", title="B", severity=Severity.P1, category="missing_file"),
        ]
    )

    _record_open_defects(state, report)  # type: ignore[arg-type]
    _enter_fix_cycle_or_escalate(state, machine, report, step_id="review_dev")  # type: ignore[arg-type]

    assert machine.to_dict()["defect_attempts"] == {"missing_file": 1}


def test_run_pipeline_stream_review_dev_requires_structured_blockers():
    step_ids = ["review_dev"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_review_output"] = "VERDICT: NEEDS_WORK\n<defect_report>{\"defects\":[]}</defect_report>"
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._max_step_retries_env",
        return_value=1,
    ):
        gen = run_pipeline_stream("input", pipeline_steps=step_ids)
        with pytest.raises(RuntimeError, match="review_dev: reviewer returned NEEDS_WORK without structured P0/P1 defects"):
            list(gen)


def test_run_pipeline_stream_review_qa_retries_dev_until_structured_defects_closed(tmp_path):
    step_ids = ["dev", "qa", "review_qa"]
    call_counts = {"dev": 0, "qa": 0, "review_qa": 0}

    def _report(defects):
        return f"<defect_report>{json.dumps({'defects': defects, 'test_scenarios': [], 'edge_cases': [], 'regression_checks': []})}</defect_report>"

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        call_counts[step_id] += 1
        if step_id == "dev":
            state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        elif step_id == "qa":
            state["qa_output"] = "QA completed"
            if call_counts["qa"] == 1:
                state["qa_defect_report"] = {
                    "defects": [
                        {
                            "id": "DEF-qa-1",
                            "title": "Missing regression coverage",
                            "severity": "P1",
                            "file_paths": ["app.py"],
                            "expected": "Regression covered",
                            "actual": "Coverage missing",
                            "repro_steps": ["Run QA"],
                            "acceptance": ["Add regression coverage"],
                            "category": "regression",
                            "fixed": False,
                        }
                    ],
                    "test_scenarios": [],
                    "edge_cases": [],
                    "regression_checks": ["Regression path"],
                }
            else:
                state["qa_defect_report"] = {"defects": [], "test_scenarios": [], "edge_cases": [], "regression_checks": []}
        elif step_id == "review_qa":
            if call_counts["review_qa"] == 1:
                state["qa_review_output"] = (
                    "VERDICT: NEEDS_WORK\n"
                    + _report(
                        [
                            {
                                "id": "DEF-review-1",
                                "title": "Behavior not verified",
                                "severity": "P1",
                                "file_paths": ["app.py"],
                                "expected": "Scenario verified",
                                "actual": "Scenario missing",
                                "repro_steps": ["Run QA review"],
                                "acceptance": ["Verify scenario"],
                                "category": "regression",
                                "fixed": False,
                            }
                        ]
                    )
                )
                state["qa_review_defect_report"] = {
                    "defects": [
                        {
                            "id": "DEF-review-1",
                            "title": "Behavior not verified",
                            "severity": "P1",
                            "file_paths": ["app.py"],
                            "expected": "Scenario verified",
                            "actual": "Scenario missing",
                            "repro_steps": ["Run QA review"],
                            "acceptance": ["Verify scenario"],
                            "category": "regression",
                            "fixed": False,
                        }
                    ],
                    "test_scenarios": [],
                    "edge_cases": [],
                    "regression_checks": ["Verify scenario"],
                }
            else:
                state["qa_review_output"] = "VERDICT: OK\n" + _report([])
                state["qa_review_defect_report"] = {"defects": [], "test_scenarios": [], "edge_cases": [], "regression_checks": []}
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        step_ids, create=True,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": str(tmp_path),
            "workspace_apply_writes": True,
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: ("Running", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        side_effect=lambda state: dict(state),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._max_step_retries_env",
        return_value=1,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        events, final_state = _drain_generator_with_return(
            run_pipeline_stream("input", pipeline_steps=step_ids)
        )

    assert call_counts["dev"] == 2
    assert call_counts["qa"] == 2
    assert call_counts["review_qa"] == 2
    assert any(
        e.get("agent") == "orchestrator" and "review_qa returned NEEDS_WORK" in e.get("message", "")
        for e in events
    )
    assert final_state["pipeline_phase"] == "DONE"
    assert final_state["open_defects"] == []


# ---------------------------------------------------------------------------
# run_pipeline_stream_resume
# ---------------------------------------------------------------------------

def test_run_pipeline_stream_resume_unknown_human_step():
    partial_state = {"input": "test", "agent_config": {}}
    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.HUMAN_PIPELINE_STEP_TO_STATE_KEY",
        {},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ):
        gen = run_pipeline_stream_resume(
            partial_state, ["pm", "human_spec", "ba"],
            "human_unknown", "human text",
        )
        with pytest.raises(ValueError, match="Unknown human gate step"):
            list(gen)


def test_run_pipeline_stream_resume_step_not_in_pipeline():
    # Non-human steps that aren't in pipeline_steps must raise ValueError("не найден").
    # (Human-prefixed steps get dynamically injected, so use a non-human gate name.)
    partial_state = {"input": "test", "agent_config": {}}
    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.HUMAN_PIPELINE_STEP_TO_STATE_KEY",
        {"review_spec": "spec_review_output"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.format_human_resume_output",
        return_value="formatted",
    ):
        gen = run_pipeline_stream_resume(
            partial_state, ["pm", "ba"],
            "review_spec", "feedback text",
        )
        with pytest.raises(ValueError, match="not found"):
            list(gen)


def test_run_pipeline_stream_resume_yields_events():
    partial_state = {"input": "test", "agent_config": {}, "pm_output": "pm done"}
    step_ids = ["pm", "human_spec", "ba"]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.HUMAN_PIPELINE_STEP_TO_STATE_KEY",
        {"human_spec": "spec_human_output"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.format_human_resume_output",
        return_value="human approved",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ):
        gen = run_pipeline_stream_resume(
            partial_state, step_ids, "human_spec", "feedback"
        )
        events = list(gen)

    # Should process 'ba' (after human_spec)
    agent_ids = [e.get("agent") for e in events]
    assert "ba" in agent_ids


def test_run_pipeline_stream_resume_runs_verification_layer_after_dev(tmp_path):
    partial_state = {
        "input": "test",
        "agent_config": {},
        "pipeline_machine": {"phase": "FIX", "fix_cycles": 1, "defect_attempts": {"regression": 1}},
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
    }
    step_ids = ["human_dev", "dev"]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.HUMAN_PIPELINE_STEP_TO_STATE_KEY",
        {"human_dev": "dev_human_output"},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph.format_human_resume_output",
        return_value="human approved",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        events, final_state = _drain_generator_with_return(
            run_pipeline_stream_resume(partial_state, step_ids, "human_dev", "feedback")
        )

    assert any(e.get("agent") == "verification_layer" for e in events)
    assert final_state["pipeline_phase"] == "DONE"


# ---------------------------------------------------------------------------
# run_pipeline_stream_retry
# ---------------------------------------------------------------------------

def test_run_pipeline_stream_retry_step_not_found():
    partial_state = {"agent_config": {}}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ):
        gen = run_pipeline_stream_retry(
            partial_state, ["pm", "ba"], "nonexistent_step"
        )
        with pytest.raises(ValueError, match="not found"):
            list(gen)


def test_run_pipeline_stream_retry_merges_override_agent_config():
    partial_state = {"agent_config": {"model": "llama3", "ba": {"model": "old"}}}
    override = {"ba": {"model": "new-model"}, "top_level": "value"}
    captured_state = {}

    def fake_validate(steps, ac):
        captured_state.update(ac)

    def fake_resolve(sid, ac):
        return "Running", _make_step_func()

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        fake_validate,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=fake_resolve,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: "Running",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ):
        gen = run_pipeline_stream_retry(
            partial_state, ["pm", "ba"], "pm",
            override_agent_config=override,
        )
        list(gen)

    # top_level key should be merged
    assert captured_state.get("top_level") == "value"
    # nested merge
    assert captured_state.get("ba", {}).get("model") == "new-model"


def test_run_pipeline_stream_retry_yields_from_step():
    partial_state = {"agent_config": {}, "pm_output": "pm done"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ):
        gen = run_pipeline_stream_retry(
            partial_state, ["pm", "ba", "dev"], "ba"
        )
        events = list(gen)

    # Should run ba and dev (from "ba" inclusive)
    agent_ids = [e.get("agent") for e in events if e.get("status") == "in_progress"]
    assert "ba" in agent_ids
    assert "dev" in agent_ids
    assert "pm" not in agent_ids


def test_run_pipeline_stream_retry_runs_verification_layer_after_dev(tmp_path):
    partial_state = {
        "agent_config": {},
        "workspace_root": str(tmp_path),
        "workspace_apply_writes": True,
        "pipeline_machine": {"phase": "FIX", "fix_cycles": 1, "defect_attempts": {}},
    }

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.validate_pipeline_steps",
        lambda *a, **kw: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._migrate_legacy_pm_tasks_state",
        lambda s: None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        events, final_state = _drain_generator_with_return(
            run_pipeline_stream_retry(partial_state, ["dev"], "dev")
        )

    assert any(e.get("agent") == "verification_layer" for e in events)
    assert final_state["pipeline_phase"] == "DONE"


# ---------------------------------------------------------------------------
# validate_pipeline_stages
# ---------------------------------------------------------------------------


def test_validate_pipeline_stages_empty():
    with pytest.raises(ValueError, match="non-empty"):
        validate_pipeline_stages([])


def test_validate_pipeline_stages_empty_stage():
    with pytest.raises(ValueError, match="non-empty list of step IDs"):
        validate_pipeline_stages([["pm"], []])


def test_validate_pipeline_stages_duplicate():
    with pytest.raises(ValueError, match="Duplicate"):
        validate_pipeline_stages([["pm"], ["pm", "ba"]])


def test_validate_pipeline_stages_clarify_input_not_first():
    with pytest.raises(ValueError, match="clarify_input must be the sole step"):
        validate_pipeline_stages([["pm", "clarify_input"]])


def test_validate_pipeline_stages_clarify_input_parallel():
    with pytest.raises(ValueError, match="clarify_input must be the sole step"):
        validate_pipeline_stages([["clarify_input", "pm"]])


def test_validate_pipeline_stages_valid():
    """Valid stages with clarify_input alone in first stage."""
    with patch(
        "backend.App.orchestration.application.routing.step_registry.validate_pipeline_steps",
        lambda steps, ac: None,
    ):
        validate_pipeline_stages(
            [["clarify_input"], ["pm"], ["ba", "architect"], ["dev"], ["qa"]]
        )


def test_validate_pipeline_stages_no_clarify_input():
    """Valid stages without clarify_input (user may omit it)."""
    with patch(
        "backend.App.orchestration.application.routing.step_registry.validate_pipeline_steps",
        lambda steps, ac: None,
    ):
        validate_pipeline_stages([["pm"], ["ba", "architect"], ["dev"]])


# ---------------------------------------------------------------------------
# run_pipeline_stream_staged
# ---------------------------------------------------------------------------


def test_run_pipeline_stream_staged_single_steps():
    """All single-step stages behave like linear runner."""
    stages = [["pm"], ["ba"], ["dev"]]

    def fake_run_step_with_stream_progress(step_id, step_func, state):
        if step_id == "dev":
            state["dev_output"] = '<swarm_file path="app.py">print("ok")</swarm_file>'
        yield {"agent": step_id, "status": "progress", "message": "working"}

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={
            "input": "test",
            "agent_config": {},
            "workspace_root": "/tmp/workspace",
            "workspace_apply_writes": True,
        },
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=fake_run_step_with_stream_progress,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.apply_from_devops_and_dev_outputs",
        return_value={"written": ["app.py"], "patched": [], "udiff_applied": [], "parsed": 1},
    ), patch(
        "backend.App.orchestration.application.enforcement.gate_runner.run_all_gates",
        return_value=[GateResult(True, "build_gate")],
    ):
        events, final_state = _drain_generator_with_return(run_pipeline_stream_staged("test", stages))

    in_progress = [e for e in events if e.get("status") == "in_progress"]
    completed = [e for e in events if e.get("status") == "completed"]
    assert len(in_progress) == 3
    assert len(completed) == 4
    assert any(e.get("agent") == "verification_layer" for e in events)
    assert final_state["pipeline_phase"] == "DONE"


def test_run_pipeline_stream_staged_parallel_stage():
    """Parallel stage emits activeSteps event and runs all steps."""
    stages = [["pm"], ["ba", "architect"], ["dev"]]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ):
        gen = run_pipeline_stream_staged("test", stages)
        events = list(gen)

    # Should have an active_steps event for the parallel stage
    active_steps_events = [e for e in events if e.get("type") == "active_steps"]
    assert len(active_steps_events) == 1
    assert set(active_steps_events[0]["activeSteps"]) == {"ba", "architect"}

    # All steps should complete
    completed = [e for e in events if e.get("status") == "completed"]
    completed_agents = {e["agent"] for e in completed}
    assert {"pm", "ba", "architect", "dev"} == completed_agents


def test_run_pipeline_stream_staged_rejects_parallel_non_plan_stage():
    stages = [["pm"], ["dev", "qa"]]

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph._initial_pipeline_state",
        return_value={"input": "test", "agent_config": {}},
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._compact_state_if_needed",
        return_value=None,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._resolve_pipeline_step",
        side_effect=lambda sid, ac: (f"Running {sid}", _make_step_func()),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_executor.run",
        side_effect=lambda sid, fn, st: iter([]),
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners._step_extractor.emit_completed",
        side_effect=lambda sid, st: {"agent": sid, "status": "completed"},
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_display.pipeline_step_in_progress_message",
        side_effect=lambda sid, st: f"Running {sid}",
    ), patch(
        "backend.App.orchestration.application.routing.pipeline_graph._state_snapshot",
        lambda s: dict(s),
    ):
        gen = run_pipeline_stream_staged("test", stages)
        with pytest.raises(ValueError, match="Parallel staged execution is only supported for PLAN-phase steps"):
            list(gen)


# ---------------------------------------------------------------------------
# stream_chat_chunks routing (UI → staged vs sequential runner)
# ---------------------------------------------------------------------------


def test_stream_chat_chunks_routes_stages_to_staged_runner(tmp_path):
    """If pipeline_stages has a parallel stage, _stream_chat_chunks must call the
    staged runner rather than the sequential one (integration of §3 UI→backend
    contract: stages declared by the UI are what drives execution)."""
    from backend.App.orchestration.application.streaming import chat_stream as stream_handlers

    called: dict[str, Any] = {}

    def fake_staged(*args: Any, **kwargs: Any):
        called["staged"] = (args, kwargs)
        return iter([])

    def fake_sequential(*args: Any, **kwargs: Any):
        called["sequential"] = (args, kwargs)
        return iter([])

    class _FakeTaskStore:
        def update_task(self, *a: Any, **kw: Any) -> None:
            pass

        def get_task(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {}

    with patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners.run_pipeline_stream_staged",
        side_effect=fake_staged,
    ), patch.object(
        stream_handlers,
        "run_pipeline_stream",
        side_effect=fake_sequential,
    ), patch.object(
        stream_handlers,
        "PipelineSSEHandler",
    ) as MockHandler:
        MockHandler.return_value.handle_events.return_value = iter([])
        list(
            stream_handlers.stream_chat_chunks(
                original_prompt="hi",
                effective_prompt="hi",
                request_model="m",
                task_id="t1",
                task_store=_FakeTaskStore(),
                artifacts_root=tmp_path,
                agent_config={},
                pipeline_steps=["pm", "ba", "architect", "dev"],
                pipeline_stages=[["pm"], ["ba", "architect"], ["dev"]],
            )
        )

    assert "staged" in called, "Parallel stage must route to staged runner"
    assert "sequential" not in called, "Sequential runner must not be used when stages declared"


def test_stream_chat_chunks_routes_linear_to_sequential_runner(tmp_path):
    """If pipeline_stages is None or all stages have one step, use the
    sequential runner (unchanged default behaviour)."""
    from backend.App.orchestration.application.streaming import chat_stream as stream_handlers

    called: dict[str, Any] = {}

    def fake_staged(*args: Any, **kwargs: Any):
        called["staged"] = True
        return iter([])

    def fake_sequential(*args: Any, **kwargs: Any):
        called["sequential"] = True
        return iter([])

    class _FakeTaskStore:
        def update_task(self, *a: Any, **kw: Any) -> None:
            pass

        def get_task(self, *a: Any, **kw: Any) -> dict[str, Any]:
            return {}

    with patch(
        "backend.App.orchestration.application.pipeline.pipeline_runners.run_pipeline_stream_staged",
        side_effect=fake_staged,
    ), patch.object(
        stream_handlers,
        "run_pipeline_stream",
        side_effect=fake_sequential,
    ), patch.object(
        stream_handlers,
        "PipelineSSEHandler",
    ) as MockHandler:
        MockHandler.return_value.handle_events.return_value = iter([])
        list(
            stream_handlers.stream_chat_chunks(
                original_prompt="hi",
                effective_prompt="hi",
                request_model="m",
                task_id="t1",
                task_store=_FakeTaskStore(),
                artifacts_root=tmp_path,
                agent_config={},
                pipeline_steps=["pm", "ba", "dev"],
                pipeline_stages=None,
            )
        )

    assert "sequential" in called
    assert "staged" not in called


# ---------------------------------------------------------------------------
# PipelineSSEHandler tolerance for meta-events without an "agent" key
# (regression for KeyError('agent') on parallel staged runs — see
# `run_pipeline_stream_staged` yielding ``{"type": "active_steps", ...}``)
# ---------------------------------------------------------------------------


def _consume_handler_events(events, tmp_path):
    """Drive PipelineSSEHandler.handle_events with a list of pipeline events."""
    from backend.App.orchestration.application.streaming.pipeline_sse_handler import PipelineSSEHandler

    class _FakeTaskStore:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        def update_task(self, *_a: Any, **kw: Any) -> None:
            self.updates.append(kw)

        def get_task(self, *_a: Any, **_kw: Any) -> dict[str, Any]:
            return {}

    written: list[tuple[str, str]] = []

    def _artifact_writer(_dir, agent, text):
        written.append((agent, text))

    task_dir = tmp_path / "task"
    agents_dir = task_dir / "agents"
    task_dir.mkdir(parents=True, exist_ok=True)
    agents_dir.mkdir(parents=True, exist_ok=True)

    store = _FakeTaskStore()
    handler = PipelineSSEHandler(task_store=store, artifact_writer=_artifact_writer)
    sse_chunks = list(
        handler.handle_events(
            events_gen=iter(events),
            task_id="t-staged",
            task_dir=task_dir,
            agents_dir=agents_dir,
            pipeline_snapshot={},
            now=1700000000,
            request_model="test-model",
            workspace_path=None,
            workspace_apply_writes=False,
            cancel_event=None,
        )
    )
    return sse_chunks, store.updates, written, task_dir


def test_pipeline_sse_handler_forwards_active_steps_meta_event(tmp_path):
    """Regression: ``run_pipeline_stream_staged`` yields a ``type=active_steps``
    meta-event without an ``agent`` key when entering a parallel stage.

    The handler must NOT crash with ``KeyError('agent')`` — it should forward
    the message as a plain delta line and continue with subsequent per-agent
    events.
    """
    events = [
        {"agent": "pm", "status": "in_progress", "message": "PM start"},
        {"agent": "pm", "status": "completed", "message": "PM done"},
        # Meta-event from staged runner — no "agent" key
        {
            "type": "active_steps",
            "activeSteps": ["ba", "architect"],
            "stage": 1,
            "status": "in_progress",
            "message": "Running parallel stage: ba, architect",
        },
        {"agent": "ba", "status": "in_progress", "message": "BA start"},
        {"agent": "architect", "status": "in_progress", "message": "Arch start"},
        {"agent": "ba", "status": "completed", "message": "BA done"},
        {"agent": "architect", "status": "completed", "message": "Arch done"},
    ]

    sse_chunks, updates, written, task_dir = _consume_handler_events(events, tmp_path)

    # No exception bubbled up — the meta-event was forwarded as a generic
    # ``[orchestrator]`` delta line, not crashed on missing ``agent`` key.
    meta_chunks = [c for c in sse_chunks if "Running parallel stage: ba, architect" in c]
    assert len(meta_chunks) == 1
    assert "[orchestrator]" in meta_chunks[0]

    # And per-agent events that came after the meta-event still drove the
    # task store + artifact writer normally — i.e. the loop did not abort.
    assert any(u.get("agent") == "ba" for u in updates)
    assert any(u.get("agent") == "architect" for u in updates)
    assert ("ba", "BA done") in written
    assert ("architect", "Arch done") in written

    # The pipeline_run.log received the meta-event line.
    log_text = (task_dir / "pipeline_run.log").read_text(encoding="utf-8")
    assert "[orchestrator] Running parallel stage: ba, architect" in log_text


def test_pipeline_sse_handler_skips_meta_event_with_no_message(tmp_path):
    """A meta-event without an ``agent`` AND without a ``message`` is silently
    dropped (no extra delta line, no log entry, no crash)."""
    events = [
        {"agent": "pm", "status": "completed", "message": "PM done"},
        {"type": "active_steps", "activeSteps": ["ba"], "stage": 1, "status": "in_progress"},
    ]

    sse_chunks, _, _, task_dir = _consume_handler_events(events, tmp_path)

    # Nothing about active_steps reached the SSE stream or the log.
    assert not any("active_steps" in c or "Running parallel" in c for c in sse_chunks)
    log_text = (task_dir / "pipeline_run.log").read_text(encoding="utf-8")
    assert "active_steps" not in log_text


def test_pipeline_sse_handler_emits_auto_approved_as_json(tmp_path):
    """M-14 — auto_approved audit events must travel as JSON in delta.content
    so the frontend's ``parseChatStreamEvent`` can distinguish them from
    regular log text and surface a toast.

    Plain ``[step] auto_approved:`` frames (the generic agent-event format)
    don't start with ``{`` and the frontend parser returns null for them.
    """
    events = [
        {
            "agent": "human_dev",
            "status": "auto_approved",
            "step": "human_dev",
            "rule": "low-risk-edit",
            "timestamp": "2026-04-16T10:00:00+00:00",
            "content_hash": "abc123",
        },
    ]

    sse_chunks, _, _, _ = _consume_handler_events(events, tmp_path)

    # Find the chunk that carries the audit payload.
    # The status string is JSON-escaped inside the outer envelope (``\"auto_approved\"``),
    # so filter on the unquoted substring instead.
    audit_chunks = [c for c in sse_chunks if "auto_approved" in c]
    assert len(audit_chunks) == 1, f"expected 1 auto_approved chunk, got {audit_chunks!r}"

    # The delta.content must be a JSON object (frontend parser requirement).
    import json
    frame = audit_chunks[0]
    # SSE layout: ``data: {"id":...,"choices":[{"delta":{"content":"<JSON>"}}]}``
    data_line = [ln for ln in frame.splitlines() if ln.startswith("data: ")][0]
    envelope = json.loads(data_line[len("data: "):])
    content = envelope["choices"][0]["delta"]["content"]
    assert content.strip().startswith("{"), f"expected JSON content, got {content!r}"
    payload = json.loads(content)
    assert payload["status"] == "auto_approved"
    assert payload["step"] == "human_dev"
    assert payload["rule"] == "low-risk-edit"
    assert payload["content_hash"] == "abc123"
