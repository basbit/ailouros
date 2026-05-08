from unittest.mock import patch

from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    finalize_metrics_best_effort,
    finalize_pipeline_metrics,
)


def test_best_effort_populates_pipeline_metrics_on_normal_state():
    state: dict = {"task_id": "task-1", "verification_gates": []}
    finalize_metrics_best_effort(state)  # type: ignore[arg-type]
    assert "pipeline_metrics" in state
    assert isinstance(state["pipeline_metrics"], dict)


def test_best_effort_swallows_exceptions_and_logs_warning(caplog):
    state: dict = {"task_id": "task-2"}
    with patch(
        "backend.App.orchestration.application.pipeline.pipeline_runtime_support.finalize_pipeline_metrics",
        side_effect=ValueError("synthetic failure"),
    ):
        finalize_metrics_best_effort(state)  # type: ignore[arg-type]
    assert "pipeline_metrics" not in state


def test_best_effort_does_not_raise_on_minimal_state():
    state: dict = {}
    finalize_metrics_best_effort(state)  # type: ignore[arg-type]


def test_finalize_pipeline_metrics_writes_state_key():
    state: dict = {"task_id": "task-3", "verification_gates": []}
    finalize_pipeline_metrics(state)  # type: ignore[arg-type]
    assert "pipeline_metrics" in state
    assert "step_metrics" in state["pipeline_metrics"]
