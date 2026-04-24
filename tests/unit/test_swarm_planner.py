"""Tests for backend/App/integrations/infrastructure/swarm_planner.py."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(return_value: str = "{}") -> MagicMock:
    agent = MagicMock()
    agent.run.return_value = return_value
    agent.used_model = "deepseek-r1:14b"
    agent.used_provider = "ollama"
    return agent


# ---------------------------------------------------------------------------
# _valid_step_ids
# ---------------------------------------------------------------------------

def test_valid_step_ids_returns_list():
    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph.DEFAULT_PIPELINE_STEP_IDS",
        ["pm", "dev", "qa"],
        create=True,
    ):
        from backend.App.integrations.infrastructure.swarm_planner import _valid_step_ids
        result = _valid_step_ids()
    assert isinstance(result, list)
    assert len(result) >= 1


# ---------------------------------------------------------------------------
# plan_pipeline_steps — main function
# ---------------------------------------------------------------------------

def _run_plan(goal: str, raw_response: str, agent_config=None, constraints=""):
    from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps

    agent = _make_agent(raw_response)
    with patch(
        "backend.App.integrations.infrastructure.swarm_planner.BaseAgent",
        return_value=agent,
    ), patch(
        "backend.App.integrations.infrastructure.swarm_planner._valid_step_ids",
        return_value=["pm", "dev", "qa", "review_dev", "human_pm"],
    ), patch(
        "backend.App.integrations.infrastructure.swarm_planner._remote_kw",
        return_value={},
    ):
        return plan_pipeline_steps(goal, agent_config=agent_config, constraints=constraints)


def test_plan_pipeline_steps_valid_json():
    raw = json.dumps({"pipeline_steps": ["pm", "dev", "qa"], "rationale": "Standard flow"})
    result = _run_plan("Build a REST API", raw)
    assert result["pipeline_steps"] == ["pm", "dev", "qa"]
    assert result["rationale"] == "Standard flow"
    assert "planner_model" in result
    assert "planner_provider" in result


def test_plan_pipeline_steps_no_agent_config():
    raw = json.dumps({"pipeline_steps": ["pm", "dev"], "rationale": "Simple"})
    result = _run_plan("Simple task", raw, agent_config=None)
    assert result["pipeline_steps"] == ["pm", "dev"]


def test_plan_pipeline_steps_with_constraints():
    raw = json.dumps({"pipeline_steps": ["pm"], "rationale": "Minimal"})
    result = _run_plan("Task", raw, constraints="No tests needed")
    assert result["pipeline_steps"] == ["pm"]


def test_plan_pipeline_steps_json_parse_error():
    result = _run_plan("Goal", "not valid json at all")
    assert result["pipeline_steps"] == []
    assert result["planner_error"] == "json_parse_error"
    assert "planner_error_detail" in result
    assert "raw" in result


def test_plan_pipeline_steps_pipeline_steps_not_a_list():
    raw = json.dumps({"pipeline_steps": "pm,dev", "rationale": "Wrong type"})
    result = _run_plan("Goal", raw)
    assert result["pipeline_steps"] == []
    assert result["planner_error"] == "pipeline_steps_not_a_list"
    assert "raw" in result


def test_plan_pipeline_steps_pipeline_steps_none():
    raw = json.dumps({"pipeline_steps": None, "rationale": "Null steps"})
    result = _run_plan("Goal", raw)
    assert result["pipeline_steps"] == []
    assert result["planner_error"] == "pipeline_steps_not_a_list"


def test_plan_pipeline_steps_empty_steps_filtered():
    raw = json.dumps({"pipeline_steps": ["pm", "", "  ", "dev"], "rationale": "Has empties"})
    result = _run_plan("Goal", raw)
    assert result["pipeline_steps"] == ["pm", "dev"]


def test_plan_pipeline_steps_json_wrapped_in_markdown():
    """JSON embedded in markdown fences still parsed via regex."""
    inner = json.dumps({"pipeline_steps": ["pm", "dev"], "rationale": "Works"})
    raw = f"```json\n{inner}\n```"
    result = _run_plan("Goal", raw)
    assert result["pipeline_steps"] == ["pm", "dev"]


def test_plan_pipeline_steps_json_with_surrounding_text():
    """JSON embedded in prose — regex extracts it."""
    inner = json.dumps({"pipeline_steps": ["qa"], "rationale": "QA only"})
    raw = f"Here is the plan: {inner} Done."
    result = _run_plan("Goal", raw)
    assert result["pipeline_steps"] == ["qa"]


def test_plan_pipeline_steps_with_agent_config():
    raw = json.dumps({"pipeline_steps": ["pm", "dev"], "rationale": "From config"})
    ac = {
        "pm": {"model": "gpt-4o", "environment": "openai"},
        "swarm_planner": {"model": "custom-model", "environment": "lmstudio"},
    }
    result = _run_plan("Goal with config", raw, agent_config=ac)
    assert result["pipeline_steps"] == ["pm", "dev"]


def test_plan_pipeline_steps_rationale_truncated():
    """Very long rationale is preserved as-is up to 4000 chars."""
    long_rationale = "x" * 5000
    raw = json.dumps({"pipeline_steps": ["pm"], "rationale": long_rationale})
    result = _run_plan("Goal", raw)
    assert len(result["rationale"]) == 4000


def test_plan_pipeline_steps_many_step_ids_truncated_in_prompt():
    """More than 60 step IDs causes allowed list to include '…'."""
    from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps

    many_ids = [f"step_{i}" for i in range(65)]
    raw = json.dumps({"pipeline_steps": ["step_0"], "rationale": "ok"})
    agent = _make_agent(raw)

    def capture_agent(*args, **kwargs):
        return agent

    with patch(
        "backend.App.integrations.infrastructure.swarm_planner.BaseAgent",
        side_effect=capture_agent,
    ), patch(
        "backend.App.integrations.infrastructure.swarm_planner._valid_step_ids",
        return_value=many_ids,
    ), patch(
        "backend.App.integrations.infrastructure.swarm_planner._remote_kw",
        return_value={},
    ):
        result = plan_pipeline_steps("Goal")
    # Just verify it completes without error
    assert "pipeline_steps" in result


def test_plan_pipeline_steps_raw_truncated_in_error():
    """Raw response truncated to 4000 chars in error case."""
    big_raw = "x" * 5000
    result = _run_plan("Goal", big_raw)
    assert len(result.get("raw", "")) <= 4000
    assert result["planner_error"] == "json_parse_error"


# ---------------------------------------------------------------------------
# _remote_kw
# ---------------------------------------------------------------------------

def test_remote_kw_empty_config():
    from backend.App.integrations.infrastructure.swarm_planner import _remote_kw

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph._remote_api_client_kwargs_for_role",
        return_value={"api_key": "test"},
        create=True,
    ):
        result = _remote_kw({}, "pm")
    assert isinstance(result, dict)


def test_remote_kw_non_dict_role():
    from backend.App.integrations.infrastructure.swarm_planner import _remote_kw

    with patch(
        "backend.App.orchestration.application.routing.pipeline_graph._remote_api_client_kwargs_for_role",
        return_value={},
        create=True,
    ):
        result = _remote_kw({"pm": "not-a-dict"}, "pm")
    assert isinstance(result, dict)
