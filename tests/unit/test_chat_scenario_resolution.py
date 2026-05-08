"""Тесты для ChatRequestResolver и resolve_chat_request с scenario_id."""

import types

import pytest

from backend.App.orchestration.application.use_cases.chat_request_resolver import (
    ChatRequest,
    ChatRequestResolver,
)


def _req(**kwargs) -> types.SimpleNamespace:
    defaults = {
        "agent_config": None,
        "pipeline_steps": None,
        "pipeline_preset": None,
        "pipeline_stages": None,
        "scenario_id": None,
        "workspace_write": False,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def test_no_scenario_id_behaves_as_before():
    req = _req(pipeline_steps=["pm", "ba"])
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert isinstance(result, ChatRequest)
    assert result.pipeline_steps == ["pm", "ba"]
    assert result.resolved_scenario is None


def test_scenario_id_code_review_resolves_steps():
    req = _req(scenario_id="code_review")
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert result.resolved_scenario is not None
    assert result.resolved_scenario.scenario_id == "code_review"
    assert result.resolved_scenario.workspace_write is False
    assert len(result.pipeline_steps) > 0


def test_scenario_id_build_feature_with_explicit_steps():
    req = _req(scenario_id="build_feature", pipeline_steps=["pm"])
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert result.pipeline_steps == ["pm"]
    assert result.resolved_scenario is not None


def test_unknown_scenario_id_raises_value_error():
    req = _req(scenario_id="nonexistent_scenario_xyz")
    resolver = ChatRequestResolver()
    with pytest.raises(ValueError, match="nonexistent_scenario_xyz"):
        resolver.resolve(req)


def test_scenario_id_and_pipeline_preset_raises():
    req = _req(scenario_id="code_review", pipeline_preset="planning_loop")
    resolver = ChatRequestResolver()
    with pytest.raises(ValueError, match="Cannot use scenario_id"):
        resolver.resolve(req)


def test_scenario_overrides_skip_gates_apply():
    req = _req(
        scenario_id="build_feature",
        scenario_overrides={"build_feature": {"skip_gates": ["human_qa"]}},
    )
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert "human_qa" not in result.pipeline_steps
    assert "human_qa" in result.resolved_scenario.skipped_gates


def test_scenario_overrides_model_profile_apply():
    req = _req(
        scenario_id="code_review",
        scenario_overrides={
            "code_review": {"model_profile": {"analyze_code": "qwen2.5-coder"}},
        },
    )
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert result.resolved_scenario.model_profile_applied == {
        "analyze_code": "qwen2.5-coder",
    }
    role_cfg = result.resolved_scenario.agent_config.get("analyze_code") or {}
    assert role_cfg.get("model") == "qwen2.5-coder"


def test_scenario_overrides_for_other_scenario_ignored():
    req = _req(
        scenario_id="code_review",
        scenario_overrides={"build_feature": {"skip_gates": ["human_qa"]}},
    )
    resolver = ChatRequestResolver()
    result = resolver.resolve(req)
    assert result.resolved_scenario.skipped_gates == ()
