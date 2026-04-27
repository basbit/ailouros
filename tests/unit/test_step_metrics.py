"""Tests for backend/App/integrations/infrastructure/observability/step_metrics.py."""
from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.observability.step_metrics import (
    _extract_token_metrics,
    _guess_model,
    record_step,
    reset_for_tests,
    snapshot,
    snapshot_for_task,
    _TOKEN_KEY_INPUT,
    _TOKEN_KEY_OUTPUT,
    _TOKEN_KEY_RETRIEVED,
    _TOKEN_KEY_TOOL_CALLS,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    reset_for_tests()
    yield
    reset_for_tests()


# ---------------------------------------------------------------------------
# _guess_model
# ---------------------------------------------------------------------------

def test_guess_model_found():
    delta = {"pm_model": "gpt-4o", "other": "value"}
    assert _guess_model(delta) == "gpt-4o"


def test_guess_model_not_found():
    delta = {"pm_output": "some text"}
    assert _guess_model(delta) == ""


def test_guess_model_empty_string_value():
    delta = {"pm_model": "   "}
    assert _guess_model(delta) == ""


def test_guess_model_non_string_value():
    delta = {"pm_model": 123}
    assert _guess_model(delta) == ""


# ---------------------------------------------------------------------------
# _extract_token_metrics
# ---------------------------------------------------------------------------

def test_extract_token_metrics_all_present():
    delta = {
        _TOKEN_KEY_INPUT: 100,
        _TOKEN_KEY_OUTPUT: 200,
        _TOKEN_KEY_RETRIEVED: 50,
        _TOKEN_KEY_TOOL_CALLS: 3,
    }
    result = _extract_token_metrics(delta)
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 200
    assert result["retrieved_tokens"] == 50
    assert result["tool_calls_count"] == 3


def test_extract_token_metrics_missing_keys():
    result = _extract_token_metrics({})
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None


def test_extract_token_metrics_invalid_value():
    delta = {_TOKEN_KEY_INPUT: "not-a-number"}
    result = _extract_token_metrics(delta)
    assert result["input_tokens"] is None


def test_extract_token_metrics_none_value():
    delta = {_TOKEN_KEY_INPUT: None}
    result = _extract_token_metrics(delta)
    assert result["input_tokens"] is None


def test_extract_token_metrics_string_int():
    delta = {_TOKEN_KEY_INPUT: "150"}
    result = _extract_token_metrics(delta)
    assert result["input_tokens"] == 150


# ---------------------------------------------------------------------------
# record_step
# ---------------------------------------------------------------------------

def test_record_step_increments_count():
    record_step("pm", 100.0)
    snap = snapshot()
    assert snap["steps"]["pm"]["count"] == 1


def test_record_step_multiple_times():
    record_step("pm", 100.0)
    record_step("pm", 200.0)
    record_step("pm", 300.0)
    snap = snapshot()
    assert snap["steps"]["pm"]["count"] == 3


def test_record_step_with_task_id():
    record_step("dev", 150.0, task_id="task-123")
    from backend.App.integrations.infrastructure.observability.step_metrics import _task_last
    assert _task_last.get("dev") == "task-123"


def test_record_step_with_model():
    record_step("pm", 100.0, step_delta={"pm_model": "gpt-4o"})
    snap = snapshot()
    assert len(snap["role_model_top"]) >= 1
    models = [r["model"] for r in snap["role_model_top"]]
    assert "gpt-4o" in models


def test_record_step_with_token_metrics():
    delta = {
        _TOKEN_KEY_INPUT: 500,
        _TOKEN_KEY_OUTPUT: 300,
    }
    record_step("dev", 200.0, step_delta=delta)
    snap = snapshot()
    tokens = snap["steps"]["dev"]["tokens"]
    assert tokens["input_tokens"] == 500
    assert tokens["output_tokens"] == 300


def test_record_step_evicts_old_samples():
    """More than _max_samples values should keep only the last _max_samples."""
    from backend.App.integrations.infrastructure.observability.step_metrics import _step_duration_ms, _max_samples
    for i in range(_max_samples + 10):
        record_step("qa", float(i))
    assert len(_step_duration_ms["qa"]) <= _max_samples


def test_record_step_no_token_data():
    """No token keys in delta → no token logging."""
    record_step("pm", 50.0, step_delta={"pm_output": "some text"})
    snap = snapshot()
    assert snap["steps"]["pm"]["count"] == 1


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

def test_snapshot_empty():
    snap = snapshot()
    assert snap["steps"] == {}
    assert snap["role_model_top"] == []
    assert "updated_at" in snap


def test_snapshot_p50_ms():
    for ms in [10.0, 20.0, 30.0]:
        record_step("pm", ms)
    snap = snapshot()
    assert "p50_ms" in snap["steps"]["pm"]
    assert snap["steps"]["pm"]["p50_ms"] == 20.0


def test_snapshot_max_ms():
    for ms in [10.0, 50.0, 30.0]:
        record_step("pm", ms)
    snap = snapshot()
    assert snap["steps"]["pm"]["max_ms"] == 50.0


def test_snapshot_multiple_steps():
    record_step("pm", 100.0)
    record_step("dev", 200.0)
    snap = snapshot()
    assert "pm" in snap["steps"]
    assert "dev" in snap["steps"]


def test_snapshot_for_task_filters_other_tasks():
    record_step("pm", 100.0, task_id="task-a", step_delta={"pm_model": "gpt-4o"})
    record_step("pm", 200.0, task_id="task-b", step_delta={"pm_model": "gpt-4o-mini"})
    snap = snapshot_for_task("task-a")
    pm_row = next(row for row in snap["steps"] if row["step_id"] == "pm")
    assert pm_row["count"] == 1
    assert pm_row["max_ms"] == 100.0
    assert snap["role_model_top"][0]["model"] == "gpt-4o"


def test_snapshot_for_task_returns_array_of_rows_for_frontend():
    record_step(
        "dev", 50.0, task_id="task-x",
        step_delta={
            "dev_model": "claude",
            "_step_input_tokens": 120,
            "_step_output_tokens": 80,
            "_step_tool_calls_count": 3,
        },
    )
    snap = snapshot_for_task("task-x")
    assert isinstance(snap["steps"], list), "frontend expects steps as array of rows"
    assert len(snap["steps"]) == 1
    row = snap["steps"][0]
    assert row["step_id"] == "dev"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 80
    assert row["tool_calls_count"] == 3


# ---------------------------------------------------------------------------
# reset_for_tests
# ---------------------------------------------------------------------------

def test_reset_clears_all():
    record_step("pm", 100.0, task_id="t1", step_delta={"pm_model": "gpt-4"})
    reset_for_tests()
    snap = snapshot()
    assert snap["steps"] == {}
    assert snap["role_model_top"] == []
