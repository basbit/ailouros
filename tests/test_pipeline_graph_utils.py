"""Tests for pure utility functions in pipeline_graph.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.orchestration.application.pipeline_graph import (
    _dev_review_router,
    _dev_retry_gate_node,
    _extract_verdict,
    _quality_gate_router,
)


# ---------------------------------------------------------------------------
# _extract_verdict
# ---------------------------------------------------------------------------

def test_extract_verdict_ok():
    assert _extract_verdict("VERDICT: OK") == "OK"


def test_extract_verdict_needs_work():
    assert _extract_verdict("VERDICT: NEEDS_WORK") == "NEEDS_WORK"


def test_extract_verdict_case_insensitive():
    assert _extract_verdict("verdict: ok") == "OK"


def test_extract_verdict_in_longer_text():
    text = "Great work! VERDICT: OK All checks passed."
    assert _extract_verdict(text) == "OK"


def test_extract_verdict_empty_returns_ok():
    assert _extract_verdict("") == "OK"


def test_extract_verdict_none_returns_ok():
    assert _extract_verdict(None) == "OK"


def test_extract_verdict_no_verdict_keyword():
    assert _extract_verdict("No verdict here.") == "OK"


def test_extract_verdict_with_whitespace():
    assert _extract_verdict("VERDICT :  APPROVED") == "APPROVED"


# ---------------------------------------------------------------------------
# _quality_gate_router
# ---------------------------------------------------------------------------

def test_quality_gate_router_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "0")
    # Reload to pick up env change — patch the module-level attribute
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        False,
    ):
        result = _quality_gate_router({}, "review_dev")
    assert result == "continue"


def test_quality_gate_router_no_needs_work():
    state = {
        "step_artifacts": {
            "review_dev": {"verdict": "OK"},
        }
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        result = _quality_gate_router(state, "review_dev")
    assert result == "continue"


def test_quality_gate_router_needs_work_retry():
    state = {
        "step_artifacts": {
            "review_dev": {"verdict": "NEEDS_WORK"},
        },
        "step_retries": {},
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        result = _quality_gate_router(state, "review_dev")
    assert result == "retry"


def test_quality_gate_router_needs_work_escalate():
    state = {
        "step_artifacts": {
            "review_dev": {"verdict": "NEEDS_WORK"},
        },
        "step_retries": {"review_dev": 2},
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=2,
    ):
        result = _quality_gate_router(state, "review_dev")
    assert result == "escalate"


def test_quality_gate_router_empty_state():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        result = _quality_gate_router({}, "review_dev")
    assert result == "continue"  # no NEEDS_WORK verdict


def test_quality_gate_router_delegates_review_qa_to_structured_router():
    state = {
        "qa_review_output": "VERDICT: NEEDS_WORK",
        "qa_defect_report": {"defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}]},
        "qa_review_defect_report": {"defects": [{"id": "d2", "title": "bug2", "severity": "P1", "fixed": False}]},
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        result = _quality_gate_router(state, "review_qa")
    assert result == "retry"


# ---------------------------------------------------------------------------
# _dev_review_router
# ---------------------------------------------------------------------------

def test_dev_review_router_disabled():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        False,
    ):
        result = _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK"})
    assert result == "continue"


def test_dev_review_router_ok_verdict():
    state = {"dev_review_output": "VERDICT: OK"}
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        result = _dev_review_router(state)
    assert result == "continue"


def test_dev_review_router_needs_work_retry():
    state = {
        "dev_review_output": "VERDICT: NEEDS_WORK",
        "dev_defect_report": {
            "defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}],
        },
        "step_retries": {},
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        result = _dev_review_router(state)
    assert result == "retry"


def test_dev_review_router_needs_work_exhausted():
    state = {
        "dev_review_output": "VERDICT: NEEDS_WORK",
        "dev_defect_report": {
            "defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}],
        },
        "step_retries": {"dev": 2},
    }
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=2,
    ):
        result = _dev_review_router(state)
    assert result == "continue"  # exhausted retries → continue


def test_dev_review_router_empty_review():
    state = {"dev_review_output": ""}
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        result = _dev_review_router(state)
    assert result == "continue"


def test_dev_review_router_requires_structured_blockers():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        with pytest.raises(RuntimeError, match="review_dev: reviewer returned NEEDS_WORK without structured P0/P1 defects"):
            _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK", "dev_defect_report": {"defects": []}})


# ---------------------------------------------------------------------------
# _dev_retry_gate_node
# ---------------------------------------------------------------------------

def test_dev_retry_gate_node_increments():
    state = {
        "step_retries": {},
        "pipeline_machine": {"phase": "VERIFY", "fix_cycles": 0, "defect_attempts": {}},
    }
    with patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        result = _dev_retry_gate_node(state)
    assert result["step_retries"]["dev"] == 1


def test_dev_retry_gate_node_increments_from_existing():
    state = {
        "step_retries": {"dev": 1},
        "pipeline_machine": {"phase": "VERIFY", "fix_cycles": 0, "defect_attempts": {}},
    }
    with patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=1,
    ):
        result = _dev_retry_gate_node(state)
    assert result["step_retries"]["dev"] == 2


def test_dev_retry_gate_node_preserves_other_retries():
    state = {
        "step_retries": {"pm": 1},
        "pipeline_machine": {"phase": "VERIFY", "fix_cycles": 0, "defect_attempts": {}},
    }
    with patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        result = _dev_retry_gate_node(state)
    assert result["step_retries"]["pm"] == 1
    assert result["step_retries"]["dev"] == 1
