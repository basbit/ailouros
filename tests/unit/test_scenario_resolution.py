"""Тесты для resolve_scenario_overrides и check_required_tools."""

from backend.App.orchestration.application.scenarios.resolution import (
    check_required_tools,
    resolve_scenario_overrides,
)
from backend.App.orchestration.domain.scenarios.scenario import Scenario


def _scenario(**kwargs) -> Scenario:
    defaults = {
        "id": "test",
        "title": "Test",
        "category": "development",
        "description": "desc",
        "pipeline_steps": ("clarify_input", "pm", "ba"),
        "default_gates": (),
        "expected_artifacts": (),
        "required_tools": (),
        "workspace_write_default": False,
        "recommended_models": {},
        "agent_config_defaults": {},
        "tags": (),
    }
    defaults.update(kwargs)
    return Scenario(**defaults)


def test_explicit_pipeline_steps_override():
    sc = _scenario()
    result = resolve_scenario_overrides(sc, ["pm"], None, None)
    assert result.pipeline_steps == ["pm"]


def test_scenario_steps_used_when_no_override():
    sc = _scenario()
    result = resolve_scenario_overrides(sc, None, None, None)
    assert result.pipeline_steps == ["clarify_input", "pm", "ba"]


def test_agent_config_deep_merge_request_wins_at_role_level():
    sc = _scenario(agent_config_defaults={"swarm": {"key": "base_val", "other": "keep"}})
    result = resolve_scenario_overrides(sc, None, {"swarm": {"key": "override"}}, None)
    assert result.agent_config["swarm"]["key"] == "override"
    assert result.agent_config["swarm"]["other"] == "keep"


def test_agent_config_non_dict_request_value_replaces():
    sc = _scenario(agent_config_defaults={"dev": {"model": "base"}})
    result = resolve_scenario_overrides(sc, None, {"dev": {"model": "new"}}, None)
    assert result.agent_config["dev"]["model"] == "new"


def test_workspace_write_override_true():
    sc = _scenario(workspace_write_default=False)
    result = resolve_scenario_overrides(sc, None, None, True)
    assert result.workspace_write is True


def test_workspace_write_override_false():
    sc = _scenario(workspace_write_default=True)
    result = resolve_scenario_overrides(sc, None, None, False)
    assert result.workspace_write is False


def test_workspace_write_none_uses_default():
    sc = _scenario(workspace_write_default=True)
    result = resolve_scenario_overrides(sc, None, None, None)
    assert result.workspace_write is True


def test_check_required_tools_web_search_missing():
    sc = _scenario(required_tools=("web_search",))
    warnings = check_required_tools(sc, {}, False)
    assert any("web_search" in w for w in warnings)


def test_check_required_tools_web_search_present():
    sc = _scenario(required_tools=("web_search",))
    config = {"swarm": {"tavily_api_key": "abc"}}
    warnings = check_required_tools(sc, config, False)
    assert not any("web_search" in w for w in warnings)


def test_check_required_tools_workspace_write_missing():
    sc = _scenario(required_tools=("workspace_write",))
    warnings = check_required_tools(sc, {}, False)
    assert any("workspace_write" in w for w in warnings)


def test_check_required_tools_workspace_write_present():
    sc = _scenario(required_tools=("workspace_write",))
    warnings = check_required_tools(sc, {}, True)
    assert not any("workspace_write" in w for w in warnings)


def test_check_required_tools_mcp_filesystem_missing():
    sc = _scenario(required_tools=("mcp_filesystem",))
    warnings = check_required_tools(sc, {}, False)
    assert any("mcp_filesystem" in w for w in warnings)


def test_check_required_tools_mcp_filesystem_present():
    sc = _scenario(required_tools=("mcp_filesystem",))
    config = {"mcp": {"servers": [{"name": "fs"}]}}
    warnings = check_required_tools(sc, config, False)
    assert not any("mcp_filesystem" in w for w in warnings)


def test_check_required_tools_unknown_tool_not_recognized():
    sc = _scenario(required_tools=("future_tool",))
    warnings = check_required_tools(sc, {}, False)
    assert any("not recognized" in w for w in warnings)


def test_skip_gates_removes_gate_from_steps():
    sc = _scenario(
        pipeline_steps=("clarify_input", "pm", "human_qa"),
        default_gates=("human_qa",),
    )
    result = resolve_scenario_overrides(sc, None, None, None, ["human_qa"])
    assert "human_qa" not in result.pipeline_steps
    assert result.default_gates == ()
    assert result.skipped_gates == ("human_qa",)


def test_skip_gates_ignores_unknown_gate():
    sc = _scenario(
        pipeline_steps=("clarify_input", "pm", "human_qa"),
        default_gates=("human_qa",),
    )
    result = resolve_scenario_overrides(sc, None, None, None, ["nonexistent_gate"])
    assert "human_qa" in result.pipeline_steps
    assert result.default_gates == ("human_qa",)
    assert result.skipped_gates == ()


def test_skip_gates_with_blank_strings_no_effect():
    sc = _scenario(
        pipeline_steps=("clarify_input", "human_qa"),
        default_gates=("human_qa",),
    )
    result = resolve_scenario_overrides(sc, None, None, None, ["", "  "])
    assert "human_qa" in result.pipeline_steps
    assert result.skipped_gates == ()


def test_model_profile_overrides_role_model():
    sc = _scenario(agent_config_defaults={"dev": {"model": "base", "extra": "keep"}})
    result = resolve_scenario_overrides(
        sc, None, None, None, None, {"dev": "qwen-coder"},
    )
    assert result.agent_config["dev"]["model"] == "qwen-coder"
    assert result.agent_config["dev"]["extra"] == "keep"
    assert result.model_profile_applied == {"dev": "qwen-coder"}


def test_model_profile_creates_role_when_absent():
    sc = _scenario()
    result = resolve_scenario_overrides(
        sc, None, None, None, None, {"qa": "claude-haiku"},
    )
    assert result.agent_config["qa"] == {"model": "claude-haiku"}
    assert result.model_profile_applied == {"qa": "claude-haiku"}


def test_model_profile_skips_blank_entries():
    sc = _scenario()
    result = resolve_scenario_overrides(
        sc, None, None, None, None, {"qa": "", "  ": "foo", "dev": "ok"},
    )
    assert "qa" not in result.model_profile_applied
    assert result.model_profile_applied == {"dev": "ok"}


def test_scenario_custom_roles_flow_into_resolved_agent_config():
    sc = _scenario(
        agent_config_defaults={
            "custom_roles": {
                "web_researcher": {
                    "prompt_text": "You are a Web Researcher.",
                    "model": "qwen2.5",
                },
            },
        },
    )
    result = resolve_scenario_overrides(sc, None, None, None)
    custom_roles = result.agent_config.get("custom_roles") or {}
    assert "web_researcher" in custom_roles
    assert custom_roles["web_researcher"]["prompt_text"] == "You are a Web Researcher."
    assert custom_roles["web_researcher"]["model"] == "qwen2.5"


def test_scenario_custom_roles_request_override_wins():
    sc = _scenario(
        agent_config_defaults={
            "custom_roles": {
                "web_researcher": {"prompt_text": "default", "model": "default-model"},
            },
        },
    )
    request_config = {
        "custom_roles": {
            "web_researcher": {"model": "custom-model"},
        },
    }
    result = resolve_scenario_overrides(sc, None, request_config, None)
    custom_roles = result.agent_config["custom_roles"]
    assert custom_roles["web_researcher"]["model"] == "custom-model"
