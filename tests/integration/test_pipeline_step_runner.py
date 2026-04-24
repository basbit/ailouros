"""Tests for pipeline_step_runner.py — StepOutputExtractor.emit_completed,
primary_output_for_step, final_pipeline_user_message, task_store_agent_label,
_format_elapsed_wall, _stream_progress_heartbeat_seconds."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from backend.App.orchestration.application.pipeline.pipeline_step_runner import (
    _format_elapsed_wall,
    _stream_progress_heartbeat_seconds,
    final_pipeline_user_message,
    primary_output_for_step,
    task_store_agent_label,
    StepOutputExtractor,
    StepStreamExecutor,
)
from backend.App.orchestration.domain.exceptions import PipelineCancelled

_extractor = StepOutputExtractor()
_executor = StepStreamExecutor()


# ---------------------------------------------------------------------------
# StepOutputExtractor.emit_completed — key agent branches
# ---------------------------------------------------------------------------

def test_emit_completed_pm():
    state = {"pm_output": "pm result", "pm_model": "claude", "pm_provider": "anthropic"}
    ev = _extractor.emit_completed("pm", state)
    assert ev["agent"] == "pm"
    assert ev["status"] == "completed"
    assert ev["message"] == "pm result"
    assert ev["model"] == "claude"
    assert ev["provider"] == "anthropic"


def test_emit_completed_review_pm():
    state = {"pm_review_output": "review text"}
    ev = _extractor.emit_completed("review_pm", state)
    assert ev["message"] == "review text"


def test_emit_completed_human_pm():
    state = {"pm_human_output": "human text"}
    ev = _extractor.emit_completed("human_pm", state)
    assert ev["message"] == "human text"
    assert "model" not in ev


def test_emit_completed_ba():
    state = {"ba_output": "ba result", "ba_model": "m", "ba_provider": "p"}
    ev = _extractor.emit_completed("ba", state)
    assert ev["message"] == "ba result"


def test_emit_completed_architect():
    state = {"arch_output": "arch", "arch_model": "m", "arch_provider": "p"}
    ev = _extractor.emit_completed("architect", state)
    assert ev["message"] == "arch"


def test_emit_completed_dev():
    state = {"dev_output": "dev code", "dev_model": "sonnet", "dev_provider": "anthropic"}
    ev = _extractor.emit_completed("dev", state)
    assert ev["message"] == "dev code"
    assert ev["model"] == "sonnet"


def test_emit_completed_qa():
    state = {"qa_output": "tests passed"}
    ev = _extractor.emit_completed("qa", state)
    assert ev["message"] == "tests passed"


def test_emit_completed_review_dev():
    state = {"dev_review_output": "VERDICT: APPROVED"}
    ev = _extractor.emit_completed("review_dev", state)
    assert ev["message"] == "VERDICT: APPROVED"


def test_emit_completed_dev_lead():
    state = {"dev_lead_output": "tasks list"}
    ev = _extractor.emit_completed("dev_lead", state)
    assert ev["message"] == "tasks list"


def test_emit_completed_pm_tasks_alias():
    state = {"dev_lead_output": "tasks list"}
    ev = _extractor.emit_completed("pm_tasks", state)
    assert ev["message"] == "tasks list"


def test_emit_completed_human_dev_lead():
    state = {"dev_lead_human_output": "approved"}
    ev = _extractor.emit_completed("human_dev_lead", state)
    assert ev["message"] == "approved"


def test_emit_completed_devops():
    state = {"devops_output": "deployed", "devops_model": "m", "devops_provider": "p"}
    ev = _extractor.emit_completed("devops", state)
    assert ev["message"] == "deployed"


def test_emit_completed_spec_merge():
    state = {"spec_output": "merged spec"}
    ev = _extractor.emit_completed("spec_merge", state)
    assert ev["message"] == "merged spec"


def test_emit_completed_analyze_code():
    state = {"analyze_code_output": "code analysis"}
    ev = _extractor.emit_completed("analyze_code", state)
    assert ev["message"] == "code analysis"


def test_emit_completed_custom_role():
    state = {"crole_my_agent_output": "custom output", "crole_my_agent_model": "m"}
    ev = _extractor.emit_completed("crole_my_agent", state)
    assert ev["message"] == "custom output"


def test_emit_completed_no_model_key_omitted():
    state = {"pm_output": "result", "pm_model": "", "pm_provider": ""}
    ev = _extractor.emit_completed("pm", state)
    assert "model" not in ev
    assert "provider" not in ev


def test_emit_completed_missing_output():
    ev = _extractor.emit_completed("pm", {})
    assert ev["message"] == ""
    assert ev["status"] == "completed"


# ---------------------------------------------------------------------------
# primary_output_for_step
# ---------------------------------------------------------------------------

def test_primary_output_for_step():
    state = {"qa_output": "all tests green"}
    result = primary_output_for_step("qa", state)
    assert result == "all tests green"


def test_primary_output_for_step_empty():
    result = primary_output_for_step("pm", {})
    assert result == ""


# ---------------------------------------------------------------------------
# final_pipeline_user_message
# ---------------------------------------------------------------------------

def test_final_pipeline_user_message_custom_steps():
    state = {"ba_output": "ba done"}
    result = final_pipeline_user_message(state, pipeline_steps=["pm", "ba"])
    assert result == "ba done"


def test_final_pipeline_user_message_legacy_qa_human():
    state = {"qa_human_output": "human confirmed", "qa_output": "tests"}
    result = final_pipeline_user_message(state)
    assert result == "human confirmed"


def test_final_pipeline_user_message_legacy_qa():
    state = {"qa_output": "tests passed"}
    result = final_pipeline_user_message(state)
    assert result == "tests passed"


def test_final_pipeline_user_message_fallback_input():
    state = {"input": "original task"}
    result = final_pipeline_user_message(state)
    assert result == "original task"


# ---------------------------------------------------------------------------
# task_store_agent_label
# ---------------------------------------------------------------------------

def test_task_store_agent_label_custom_steps():
    result = task_store_agent_label({}, pipeline_steps=["pm", "dev"])
    assert result == "dev"


def test_task_store_agent_label_human_qa():
    state = {"qa_human_output": "human output"}
    result = task_store_agent_label(state)
    assert result == "human_qa"


def test_task_store_agent_label_qa():
    state = {"qa_output": "qa output"}
    result = task_store_agent_label(state)
    assert result == "qa"


def test_task_store_agent_label_default():
    result = task_store_agent_label({})
    assert result == "qa"


# ---------------------------------------------------------------------------
# _format_elapsed_wall
# ---------------------------------------------------------------------------

def test_format_elapsed_wall_seconds():
    assert _format_elapsed_wall(45) == "45s"


def test_format_elapsed_wall_minutes():
    assert _format_elapsed_wall(90) == "1m 30s"


def test_format_elapsed_wall_hours():
    assert _format_elapsed_wall(3661) == "1h 1m 1s"


def test_format_elapsed_wall_zero():
    assert _format_elapsed_wall(0) == "0s"


def test_format_elapsed_wall_negative():
    assert _format_elapsed_wall(-5) == "0s"


# ---------------------------------------------------------------------------
# _stream_progress_heartbeat_seconds
# ---------------------------------------------------------------------------

def test_stream_progress_heartbeat_default(monkeypatch):
    monkeypatch.delenv("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", raising=False)
    result = _stream_progress_heartbeat_seconds()
    assert result == 8.0


def test_stream_progress_heartbeat_from_env(monkeypatch):
    monkeypatch.setenv("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", "15")
    result = _stream_progress_heartbeat_seconds()
    assert result == 15.0


def test_stream_progress_heartbeat_min_clamp(monkeypatch):
    monkeypatch.setenv("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", "0.5")
    result = _stream_progress_heartbeat_seconds()
    assert result == 2.0


def test_stream_progress_heartbeat_max_clamp(monkeypatch):
    monkeypatch.setenv("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", "200")
    result = _stream_progress_heartbeat_seconds()
    assert result == 120.0


def test_stream_progress_heartbeat_invalid_env(monkeypatch):
    monkeypatch.setenv("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", "not_a_number")
    result = _stream_progress_heartbeat_seconds()
    assert result == 8.0


# ---------------------------------------------------------------------------
# StepOutputExtractor.emit_completed — additional branches
# ---------------------------------------------------------------------------

def test_emit_completed_review_ba():
    state = {"ba_review_output": "BA review", "ba_review_model": "m", "ba_review_provider": "p"}
    ev = _extractor.emit_completed("review_ba", state)
    assert ev["message"] == "BA review"


def test_emit_completed_human_ba():
    state = {"ba_human_output": "BA human"}
    ev = _extractor.emit_completed("human_ba", state)
    assert ev["message"] == "BA human"


def test_emit_completed_review_stack():
    state = {"stack_review_output": "stack review", "stack_review_model": "m"}
    ev = _extractor.emit_completed("review_stack", state)
    assert ev["message"] == "stack review"


def test_emit_completed_review_arch():
    state = {"arch_review_output": "arch review"}
    ev = _extractor.emit_completed("review_arch", state)
    assert ev["message"] == "arch review"


def test_emit_completed_human_arch():
    state = {"arch_human_output": "arch human"}
    ev = _extractor.emit_completed("human_arch", state)
    assert ev["message"] == "arch human"


def test_emit_completed_review_spec():
    state = {"spec_review_output": "spec review"}
    ev = _extractor.emit_completed("review_spec", state)
    assert ev["message"] == "spec review"


def test_emit_completed_human_spec():
    state = {"spec_human_output": "spec human"}
    ev = _extractor.emit_completed("human_spec", state)
    assert ev["message"] == "spec human"


def test_emit_completed_generate_documentation():
    state = {"generate_documentation_output": "docs generated", "generate_documentation_model": "m"}
    ev = _extractor.emit_completed("generate_documentation", state)
    assert ev["message"] == "docs generated"


def test_emit_completed_problem_spotter():
    state = {"problem_spotter_output": "issues found"}
    ev = _extractor.emit_completed("problem_spotter", state)
    assert ev["message"] == "issues found"


def test_emit_completed_refactor_plan():
    state = {"refactor_plan_output": "refactor plan"}
    ev = _extractor.emit_completed("refactor_plan", state)
    assert ev["message"] == "refactor plan"


def test_emit_completed_human_code_review():
    state = {"code_review_human_output": "APPROVED"}
    ev = _extractor.emit_completed("human_code_review", state)
    assert ev["message"] == "APPROVED"


def test_emit_completed_review_devops():
    state = {"devops_review_output": "devops review"}
    ev = _extractor.emit_completed("review_devops", state)
    assert ev["message"] == "devops review"


def test_emit_completed_human_devops():
    state = {"devops_human_output": "devops human"}
    ev = _extractor.emit_completed("human_devops", state)
    assert ev["message"] == "devops human"


def test_emit_completed_review_dev_lead():
    state = {"dev_lead_review_output": "lead review"}
    ev = _extractor.emit_completed("review_dev_lead", state)
    assert ev["message"] == "lead review"


def test_emit_completed_review_pm_tasks():
    state = {"dev_lead_review_output": "pm tasks review"}
    ev = _extractor.emit_completed("review_pm_tasks", state)
    assert ev["message"] == "pm tasks review"


def test_emit_completed_human_pm_tasks():
    state = {"dev_lead_human_output": "pm tasks human"}
    ev = _extractor.emit_completed("human_pm_tasks", state)
    assert ev["message"] == "pm tasks human"


def test_emit_completed_review_qa():
    state = {"qa_review_output": "QA review", "qa_review_model": "m"}
    ev = _extractor.emit_completed("review_qa", state)
    assert ev["message"] == "QA review"


def test_emit_completed_human_qa():
    state = {"qa_human_output": "QA human"}
    ev = _extractor.emit_completed("human_qa", state)
    assert ev["message"] == "QA human"


# ---------------------------------------------------------------------------
# StepStreamExecutor.run
# ---------------------------------------------------------------------------


def test_run_step_with_stream_progress_basic():
    state = {}

    def step_fn(st):
        return {"result_key": "done"}

    with patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._pipeline_should_cancel",
        return_value=False,
    ), patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._step_heartbeat_interval_sec",
        return_value=100.0,
    ):
        list(_executor.run("pm", step_fn, state))

    assert state.get("result_key") == "done"
    assert "_stream_progress_queue" not in state


def test_run_step_with_stream_progress_exception_propagated():
    state = {}

    def failing_step(st):
        raise RuntimeError("step crashed")

    with patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._pipeline_should_cancel",
        return_value=False,
    ):
        with pytest.raises(RuntimeError, match="step crashed"):
            list(_executor.run("pm", failing_step, state))

    assert "_stream_progress_queue" not in state


def test_run_step_with_stream_progress_cancel_raises():
    cancel_counter = [0]

    def step_fn(st):
        time.sleep(0.5)
        return {}

    def should_cancel(s):
        cancel_counter[0] += 1
        return cancel_counter[0] > 3

    with patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._pipeline_should_cancel",
        side_effect=should_cancel,
    ), patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._step_heartbeat_interval_sec",
        return_value=100.0,
    ):
        with pytest.raises(PipelineCancelled):
            list(_executor.run("pm", step_fn, {}))


def test_run_step_with_stream_progress_cleans_queue_on_error():
    state = {}

    def failing_step(st):
        raise ValueError("boom")

    with patch(
        "backend.App.orchestration.infrastructure.step_stream_executor._pipeline_should_cancel",
        return_value=False,
    ):
        with pytest.raises(ValueError):
            list(_executor.run("pm", failing_step, state))

    # Queue should be cleaned up
    assert "_stream_progress_queue" not in state
