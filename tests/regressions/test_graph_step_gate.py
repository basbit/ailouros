"""Regression: LangGraph must skip nodes not in user's pipeline_step_ids.

Bug: task aec02899 had topology='parallel' and review_pm in its wire
steps. The compiled LangGraph runs the full static DAG and ignores
``pipeline_step_ids`` — any future path that lands in graph mode with
a reduced step list would execute unwanted nodes (review_pm,
review_stack, …).

Expected:
  * A node wrapped with ``_wrap_with_step_gate`` no-ops (returns {})
    when its step id is not present in ``state['_pipeline_step_ids']``.
  * Empty list in state means "no filter" → original node runs.
  * Missing key in state means "no filter" → original node runs
    (preserves default LangGraph behaviour before the gate was added).
"""
from __future__ import annotations

from typing import Any

from backend.App.orchestration.application.graph_builder import (
    _wrap_with_step_gate,
)


def _counting_node():
    calls: list[dict[str, Any]] = []

    def _node(state: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(state))
        return {"ran": True}

    return _node, calls


def test_node_runs_when_step_in_list():
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("pm", fn)
    result = gated({"_pipeline_step_ids": ["clarify_input", "pm"]})
    assert result == {"ran": True}
    assert len(calls) == 1


def test_node_skipped_when_step_not_in_list():
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("review_pm", fn)
    result = gated({"_pipeline_step_ids": ["pm"]})
    assert result == {}, "gated node must return empty dict when skipped"
    assert len(calls) == 0, "inner node must not run when gated out"


def test_node_runs_when_list_is_empty():
    """Empty list = 'no filter' (keeps backward compatibility with legacy graph mode)."""
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("review_pm", fn)
    result = gated({"_pipeline_step_ids": []})
    assert result == {"ran": True}
    assert len(calls) == 1


def test_node_runs_when_state_has_no_key():
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("review_pm", fn)
    result = gated({})  # no "_pipeline_step_ids" at all
    assert result == {"ran": True}
    assert len(calls) == 1


def test_node_runs_when_key_is_none():
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("review_pm", fn)
    result = gated({"_pipeline_step_ids": None})
    assert result == {"ran": True}
    assert len(calls) == 1


def test_node_skipped_case_sensitive():
    """Step ids are lowercase; 'PM' should NOT match 'pm'."""
    fn, calls = _counting_node()
    gated = _wrap_with_step_gate("pm", fn)
    result = gated({"_pipeline_step_ids": ["PM"]})
    assert result == {}
    assert len(calls) == 0
