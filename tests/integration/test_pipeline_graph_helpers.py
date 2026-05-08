"""Tests for pipeline_graph.py helper functions: _extract_verdict, _dev_review_router,
_dev_retry_gate_node, _quality_gate_router."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.orchestration.application.routing.pipeline_graph import (
    _dev_retry_gate_node,
    _dev_review_router,
    _extract_verdict,
    _quality_gate_router,
)
from backend.App.orchestration.application.routing.graph_builder import _qa_review_router


# ---------------------------------------------------------------------------
# _extract_verdict
# ---------------------------------------------------------------------------

def test_extract_verdict_needs_work_when_no_verdict():
    assert _extract_verdict("") == "NEEDS_WORK"
    assert _extract_verdict("no verdict here") == "NEEDS_WORK"


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
    assert _extract_verdict(None) == "NEEDS_WORK"  # type: ignore[arg-type]


def test_extract_verdict_uses_last_marker():
    assert _extract_verdict("VERDICT: NEEDS_WORK\nupdated\nVERDICT: OK") == "OK"


@pytest.mark.parametrize(
    "text,expected",
    [
        # Regression for 2026-04-16 bug: reviewer wrote markdown-bold
        # ``**Verdict:** NEEDS_WORK`` and the old regex silently returned "OK",
        # skipping the dev-rework loop and letting NEEDS_WORK flow to devops/qa.
        ("**Verdict:** NEEDS_WORK", "NEEDS_WORK"),
        ("**VERDICT: NEEDS_WORK**", "NEEDS_WORK"),
        ("**Verdict**: NEEDS_WORK", "NEEDS_WORK"),
        ("Verdict: **NEEDS_WORK**", "NEEDS_WORK"),
        ("`Verdict`: OK", "OK"),
        ("### Final Verdict\nVERDICT: APPROVED", "APPROVED"),
        ("verdict  :  needs_work", "NEEDS_WORK"),
        ("verdict:APPROVED", "APPROVED"),
    ],
)
def test_extract_verdict_tolerates_markdown_emphasis(text: str, expected: str) -> None:
    """Markdown-wrapped verdicts (bold/italic/inline-code/heading) must parse.

    Regression guard: without this tolerance the review_dev → dev rework loop
    becomes unreachable whenever a reviewer uses emphasis around the label.
    """
    assert _extract_verdict(text) == expected


# ---------------------------------------------------------------------------
# _dev_review_router
# ---------------------------------------------------------------------------

def test_dev_review_router_continue_when_gate_disabled():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=False,
    ):
        result = _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK"})
    assert result == "continue"


def test_dev_review_router_continue_when_approved():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ):
        result = _dev_review_router({"dev_review_output": "VERDICT: APPROVED"})
    assert result == "continue"


def test_dev_review_router_retry_when_needs_work_no_retries():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._MAX_STEP_RETRIES",
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
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._MAX_STEP_RETRIES",
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
    assert result == "escalate"  # exhausted retries → escalate to human
    assert "escalation_warning" in state


def test_dev_review_router_requires_structured_blockers():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ):
        with pytest.raises(RuntimeError, match="review_dev: reviewer returned NEEDS_WORK without structured P0/P1 defects"):
            _dev_review_router({"dev_review_output": "VERDICT: NEEDS_WORK", "dev_defect_report": {"defects": []}})


def test_qa_review_router_retry_when_structured_blockers_present():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.pipeline.pipeline_state_helpers.get_step_retries",
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
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=False,
    ):
        result = _quality_gate_router({"step_artifacts": {}}, "pm")
    assert result == "continue"


def test_quality_gate_router_no_verdict_continues():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ):
        state = {"step_artifacts": {"pm": {"verdict": "OK"}}}
        result = _quality_gate_router(state, "pm")
    assert result == "continue"


def test_quality_gate_router_needs_work_retries_when_under_limit():
    with patch(
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._MAX_STEP_RETRIES",
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
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ), patch(
        "backend.App.orchestration.application.routing.graph_builder._MAX_STEP_RETRIES",
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
        "backend.App.orchestration.application.routing.graph_builder.is_quality_gate_enabled",
        return_value=True,
    ):
        # No artifact = no verdict = continue
        result = _quality_gate_router({}, "pm")
    assert result == "continue"
