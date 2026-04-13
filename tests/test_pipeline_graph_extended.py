"""Extended tests for pipeline_graph.py — pipeline_step_in_progress_message,
validate_pipeline_steps, _resolve_pipeline_step, _with_approval_gate."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# pipeline_step_in_progress_message
# ---------------------------------------------------------------------------

def test_step_in_progress_message_known_step():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    result = pipeline_step_in_progress_message("pm", {})
    assert isinstance(result, str)
    assert len(result) > 0


def test_step_in_progress_message_dev_with_roles():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {
        "agent_config": {
            "dev_roles": [
                {"name": "Backend", "prompt": "..."},
                {"name": "Frontend", "prompt": "..."},
            ]
        }
    }
    result = pipeline_step_in_progress_message("dev", state)
    assert "Backend" in result
    assert "Frontend" in result


def test_step_in_progress_message_dev_with_subtasks():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {
        "dev_qa_tasks": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
    }
    result = pipeline_step_in_progress_message("dev", state)
    assert "3" in result


def test_step_in_progress_message_dev_single_task():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {"dev_qa_tasks": [{"id": "t1"}]}
    result = pipeline_step_in_progress_message("dev", state)
    # With only 1 task, no subtask message
    assert "подзадач" not in result


def test_step_in_progress_message_qa_with_subtasks():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {"dev_qa_tasks": [{"id": "t1"}, {"id": "t2"}]}
    result = pipeline_step_in_progress_message("qa", state)
    assert "2" in result


def test_step_in_progress_message_qa_single_task():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {"dev_qa_tasks": [{"id": "t1"}]}
    result = pipeline_step_in_progress_message("qa", state)
    # Single task → no subtask annotation
    assert "подзадач" not in result


def test_step_in_progress_message_unknown_step():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    result = pipeline_step_in_progress_message("nonexistent_step", {})
    assert result == "nonexistent_step"


def test_step_in_progress_message_dev_no_roles_no_tasks():
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )
    state = {}
    result = pipeline_step_in_progress_message("dev", state)
    # Returns the base label from registry
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# validate_pipeline_steps
# ---------------------------------------------------------------------------

def test_validate_pipeline_steps_empty_raises():
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps
    with pytest.raises(ValueError, match="non-empty"):
        validate_pipeline_steps([])


def test_validate_pipeline_steps_valid():
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps
    # pm is always a valid step
    validate_pipeline_steps(["pm"])  # Should not raise


def test_validate_pipeline_steps_unknown_raises():
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps
    with pytest.raises(ValueError, match="Unknown pipeline step ids"):
        validate_pipeline_steps(["totally_unknown_step_xyz"])


def test_validate_pipeline_steps_with_custom_role():
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps
    ac = {
        "custom_roles": {
            "myagent": {"title": "My Agent", "prompt": "..."}
        }
    }
    validate_pipeline_steps(["crole_myagent"], agent_config=ac)  # Should not raise


def test_validate_pipeline_steps_custom_role_unknown():
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps
    with pytest.raises(ValueError, match="Unknown"):
        validate_pipeline_steps(["crole_unknownslug"])


# ---------------------------------------------------------------------------
# _resolve_pipeline_step
# ---------------------------------------------------------------------------

def test_resolve_pipeline_step_known():
    from backend.App.orchestration.application.pipeline_graph import _resolve_pipeline_step
    label, func = _resolve_pipeline_step("pm", None)
    assert isinstance(label, str)
    assert callable(func)


def test_resolve_pipeline_step_custom_role():
    from backend.App.orchestration.application.pipeline_graph import _resolve_pipeline_step
    ac = {
        "custom_roles": {
            "myagent": {"title": "My Agent Title", "prompt": "Do stuff"}
        }
    }
    label, func = _resolve_pipeline_step("crole_myagent", ac)
    assert "My Agent Title" in label
    assert callable(func)


def test_resolve_pipeline_step_custom_role_no_config_raises():
    from backend.App.orchestration.application.pipeline_graph import _resolve_pipeline_step
    with pytest.raises(KeyError):
        _resolve_pipeline_step("crole_unknown_agent", None)


def test_resolve_pipeline_step_unknown_raises():
    from backend.App.orchestration.application.pipeline_graph import _resolve_pipeline_step
    with pytest.raises(KeyError):
        _resolve_pipeline_step("nonexistent_step_abc", None)


# ---------------------------------------------------------------------------
# _with_approval_gate — auto-approve path
# ---------------------------------------------------------------------------

def test_wrap_human_gate_auto_approved():
    from backend.App.orchestration.application.pipeline_graph import (
        _with_approval_gate,
    )

    mock_policy = MagicMock()
    decision = MagicMock()
    decision.approved = True
    decision.rule_matched = "always_approve"
    mock_policy.evaluate.return_value = decision

    inner_fn = MagicMock(return_value={"output": "human_response"})

    with patch(
        "backend.App.orchestration.application.graph_builder._approval_policy",
        mock_policy,
    ):
        wrapped = _with_approval_gate("human_pm", inner_fn)
        result = wrapped({"task_id": "t1"})

    # Auto-approved → skips human wait; audit trail stored in auto_approvals
    assert "auto_approvals" in result
    assert len(result["auto_approvals"]) == 1
    assert result["auto_approvals"][0]["step"] == "human_pm"
    inner_fn.assert_not_called()


def test_wrap_human_gate_not_approved():
    from backend.App.orchestration.application.pipeline_graph import (
        _with_approval_gate,
    )

    mock_policy = MagicMock()
    decision = MagicMock()
    decision.approved = False
    mock_policy.evaluate.return_value = decision

    inner_fn = MagicMock(return_value={"human_pm_output": "approved by human"})

    with patch(
        "backend.App.orchestration.application.graph_builder._approval_policy",
        mock_policy,
    ):
        wrapped = _with_approval_gate("human_pm", inner_fn)
        result = wrapped({"task_id": "t1"})

    assert result == {"human_pm_output": "approved by human"}
    inner_fn.assert_called_once()
