"""Tests for pipeline_graph.py helper functions: _extract_verdict, _dev_review_router,
_dev_retry_gate_node, _quality_gate_router."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.orchestration.application.pipeline_graph import (
    _dev_retry_gate_node,
    _dev_review_router,
    _extract_verdict,
    _quality_gate_router,
)
from backend.App.orchestration.application.graph_builder import _qa_review_router


# ---------------------------------------------------------------------------
# _extract_verdict
# ---------------------------------------------------------------------------

def test_extract_verdict_ok_when_no_verdict():
    assert _extract_verdict("") == "OK"
    assert _extract_verdict("no verdict here") == "OK"


def test_extract_verdict_parses_needs_work():
    assert _extract_verdict("VERDICT: NEEDS_WORK\nsome text") == "NEEDS_WORK"


def test_extract_verdict_parses_approved():
    assert _extract_verdict("VERDICT: APPROVED") == "APPROVED"


def test_extract_verdict_case_insensitive():
    assert _extract_verdict("verdict: approved") == "APPROVED"


def test_extract_verdict_with_leading_text():
    text = "After review I found issues.\n\nVERDICT: NEEDS_WORK"
    assert _extract_verdict(text) == "NEEDS_WORK"


def test_extract_verdict_none_input():
    assert _extract_verdict(None) == "OK"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _dev_review_router
# ---------------------------------------------------------------------------

def test_dev_review_router_continue_when_gate_disabled():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        False,
    ):
        result = _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK"})
    assert result == "continue"


def test_dev_review_router_continue_when_approved():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        result = _dev_review_router({"dev_review_output": "VERDICT: APPROVED"})
    assert result == "continue"


def test_dev_review_router_retry_when_needs_work_no_retries():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ):
        state = {
            "dev_review_output": "VERDICT: NEEDS_WORK",
            "dev_defect_report": {
                "defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}],
            },
            "step_retries": {},
        }
        result = _dev_review_router(state)
    assert result == "retry"


def test_dev_review_router_continue_when_retries_exhausted():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ):
        state = {
            "dev_review_output": "VERDICT: NEEDS_WORK",
            "dev_defect_report": {
                "defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}],
            },
            "step_retries": {"dev": 2},
        }
        result = _dev_review_router(state)
    assert result == "continue"


def test_dev_review_router_requires_structured_blockers():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        with pytest.raises(RuntimeError, match="review_dev: reviewer returned NEEDS_WORK without structured P0/P1 defects"):
            _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK", "dev_defect_report": {"defects": []}})


def test_qa_review_router_retry_when_structured_blockers_present():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.pipeline_state_helpers.get_step_retries",
        return_value=0,
    ):
        state = {
            "qa_review_output": "VERDICT: NEEDS_WORK",
            "qa_defect_report": {"defects": [{"id": "d1", "title": "bug", "severity": "P1", "fixed": False}]},
            "qa_review_defect_report": {"defects": [{"id": "d2", "title": "bug2", "severity": "P1", "fixed": False}]},
        }
        assert _qa_review_router(state) == "retry"


# ---------------------------------------------------------------------------
# _dev_retry_gate_node
# ---------------------------------------------------------------------------

def test_dev_retry_gate_node_increments_counter():
    state = {
        "step_retries": {"dev": 1},
        "pipeline_machine": {"phase": "VERIFY", "fix_cycles": 0, "defect_attempts": {}},
    }
    result = _dev_retry_gate_node(state)  # type: ignore[arg-type]
    assert result["step_retries"]["dev"] == 2


def test_dev_retry_gate_node_from_zero():
    state: dict = {"pipeline_machine": {"phase": "VERIFY", "fix_cycles": 0, "defect_attempts": {}}}
    result = _dev_retry_gate_node(state)  # type: ignore[arg-type]
    assert result["step_retries"]["dev"] == 1


# ---------------------------------------------------------------------------
# _quality_gate_router
# ---------------------------------------------------------------------------

def test_quality_gate_router_disabled_returns_continue():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        False,
    ):
        result = _quality_gate_router({"step_artifacts": {}}, "pm")
    assert result == "continue"


def test_quality_gate_router_no_verdict_continues():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        state = {"step_artifacts": {"pm": {"verdict": "OK"}}}
        result = _quality_gate_router(state, "pm")
    assert result == "continue"


def test_quality_gate_router_needs_work_retries_when_under_limit():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ):
        state = {
            "step_artifacts": {"pm": {"verdict": "NEEDS_WORK"}},
            "step_retries": {},
        }
        result = _quality_gate_router(state, "pm")
    assert result == "retry"


def test_quality_gate_router_escalates_when_retries_exhausted():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ), patch(
        "backend.App.orchestration.application.graph_builder._MAX_STEP_RETRIES",
        2,
    ):
        state = {
            "step_artifacts": {"pm": {"verdict": "NEEDS_WORK"}},
            "step_retries": {"pm": 2},
        }
        result = _quality_gate_router(state, "pm")
    assert result == "escalate"


def test_quality_gate_router_no_artifacts_continues():
    with patch(
        "backend.App.orchestration.application.graph_builder._QUALITY_GATE_ENABLED_DEFAULT",
        True,
    ):
        # No artifact = no verdict = continue
        result = _quality_gate_router({}, "pm")
    assert result == "continue"
