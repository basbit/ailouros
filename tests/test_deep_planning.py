"""Unit tests for deep_planning.py — ISSUE-15 and related parsing logic."""
import json
from unittest.mock import patch

from backend.App.orchestration.application.deep_planning import (
    Alternative,
    DeepPlan,
    DeepPlanner,
    RiskItem,
)


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

def test_extract_json_array():
    planner = DeepPlanner()
    result = planner._extract_json('[{"id": "R1", "value": 1}]')
    assert result == [{"id": "R1", "value": 1}]


def test_extract_json_object():
    planner = DeepPlanner()
    result = planner._extract_json('{"key": "value", "count": 42}')
    assert result == {"key": "value", "count": 42}


def test_extract_json_with_markdown_fence():
    planner = DeepPlanner()
    text = '```json\n[{"id": "R1"}]\n```'
    result = planner._extract_json(text)
    assert result == [{"id": "R1"}]


def test_extract_json_with_trailing_text():
    """ISSUE-15 regression: JSON followed by plain-text must not corrupt the parse."""
    planner = DeepPlanner()
    text = (
        '[{"id": "R1", "description": "foo"}] '
        'Note: the above list contains one item with a closing ] bracket.'
    )
    result = planner._extract_json(text)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["id"] == "R1"


# ---------------------------------------------------------------------------
# _parse_risks
# ---------------------------------------------------------------------------

def test_parse_risks_valid():
    planner = DeepPlanner()
    raw = json.dumps([
        {
            "id": "R1",
            "description": "Auth bypass",
            "likelihood": "high",
            "impact": "high",
            "mitigation": "Use MFA",
        }
    ])
    risks = planner._parse_risks(raw)
    assert len(risks) == 1
    assert isinstance(risks[0], RiskItem)
    assert risks[0].id == "R1"
    assert risks[0].likelihood == "high"
    assert risks[0].mitigation == "Use MFA"


def test_parse_risks_invalid():
    planner = DeepPlanner()
    risks = planner._parse_risks("this is not json at all !!!")
    assert risks == []


# ---------------------------------------------------------------------------
# _parse_alternatives
# ---------------------------------------------------------------------------

def test_parse_alternatives_valid():
    planner = DeepPlanner()
    raw = json.dumps([
        {
            "title": "Option A",
            "description": "Do it fast",
            "pros": ["quick"],
            "cons": ["fragile"],
        },
        {
            "title": "Option B",
            "description": "Do it right",
            "pros": ["robust"],
            "cons": ["slow"],
        },
    ])
    alts = planner._parse_alternatives(raw)
    assert len(alts) == 2
    assert isinstance(alts[0], Alternative)
    assert alts[0].title == "Option A"
    assert alts[1].pros == ["robust"]


# ---------------------------------------------------------------------------
# analyze() with deep planning disabled
# ---------------------------------------------------------------------------

def test_deep_planner_disabled():
    planner = DeepPlanner()
    with patch(
        "backend.App.orchestration.application.deep_planning._deep_planning_enabled",
        return_value=False,
    ):
        plan = planner.analyze(task_id="t-001", task_spec="Build something")

    assert isinstance(plan, DeepPlan)
    assert plan.task_id == "t-001"
    assert plan.task_goal == "Build something"
    assert plan.risks == []
    assert plan.alternatives == []
    assert plan.milestones == []
    assert plan.scan_summary == ""
    assert plan.error == ""
