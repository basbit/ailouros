"""Кастомный порядок шагов (sequential runner)."""

import pytest

from langgraph_pipeline import run_pipeline, run_pipeline_stream, validate_pipeline_steps


def test_validate_pipeline_steps_rejects_unknown():
    try:
        validate_pipeline_steps(["pm", "nope"])
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_run_pipeline_custom_order_short(monkeypatch):
    monkeypatch.setattr("langgraph_pipeline.PMAgent.run", lambda self, _: "pm only")
    monkeypatch.setattr("langgraph_pipeline.ReviewerAgent.run", lambda self, _: "VERDICT: OK")

    out = run_pipeline("task", pipeline_steps=["pm", "review_pm"])
    assert out["pm_output"] == "pm only"
    assert out["pm_review_output"].startswith("VERDICT: OK")


def test_run_pipeline_stream_custom(monkeypatch):
    monkeypatch.setattr("langgraph_pipeline.PMAgent.run", lambda self, _: "x")

    events = list(
        run_pipeline_stream("t", pipeline_steps=["pm"], agent_config={})
    )
    assert events[0]["status"] == "in_progress"
    assert events[1]["agent"] == "pm"
    assert events[1]["status"] == "completed"
    assert events[1]["message"] == "x"


def test_validate_pipeline_steps_custom_role_requires_config():
    with pytest.raises(ValueError, match="crole_x"):
        validate_pipeline_steps(["pm", "crole_x"], {})
    validate_pipeline_steps(
        ["pm", "crole_x"],
        {"custom_roles": {"x": {"title": "t"}}},
    )


def test_run_pipeline_custom_role_only(monkeypatch):
    monkeypatch.setattr(
        "backend.App.orchestration.application.routing.pipeline_graph.CustomSwarmRoleAgent.run",
        lambda self, _: "custom-done",
    )
    out = run_pipeline(
        "task",
        pipeline_steps=["crole_ab"],
        agent_config={
            "custom_roles": {
                "ab": {
                    "title": "AB",
                    "prompt_text": "sys",
                    "environment": "ollama",
                    "model": "m",
                }
            }
        },
    )
    assert out["crole_ab_output"] == "custom-done"
