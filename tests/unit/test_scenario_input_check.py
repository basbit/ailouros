"""Тесты для collect_missing_required_inputs."""

from backend.App.orchestration.application.scenarios.input_check import (
    collect_missing_required_inputs,
)
from backend.App.orchestration.domain.scenarios.inputs import InputSpec
from backend.App.orchestration.domain.scenarios.scenario import Scenario


def _scenario(*specs: InputSpec) -> Scenario:
    return Scenario(
        id="test",
        title="Test",
        category="development",
        description="desc",
        pipeline_steps=("clarify_input",),
        default_gates=(),
        expected_artifacts=(),
        required_tools=(),
        workspace_write_default=False,
        recommended_models={},
        agent_config_defaults={},
        tags=(),
        inputs=tuple(specs),
    )


def test_no_inputs_means_no_missing():
    scenario = _scenario()
    missing = collect_missing_required_inputs(scenario, "prompt", None, None)
    assert missing == []


def test_required_prompt_missing_when_blank():
    scenario = _scenario(InputSpec(key="prompt", label="P", required=True))
    missing = collect_missing_required_inputs(scenario, "   ", None, None)
    assert missing == ["prompt"]


def test_required_prompt_present():
    scenario = _scenario(InputSpec(key="prompt", label="P", required=True))
    missing = collect_missing_required_inputs(scenario, "do x", None, None)
    assert missing == []


def test_required_workspace_root_missing_when_none():
    scenario = _scenario(
        InputSpec(key="workspace_root", label="W", required=True),
    )
    missing = collect_missing_required_inputs(scenario, "p", None, None)
    assert missing == ["workspace_root"]


def test_required_workspace_root_missing_when_blank_string():
    scenario = _scenario(
        InputSpec(key="workspace_root", label="W", required=True),
    )
    missing = collect_missing_required_inputs(scenario, "p", "  ", None)
    assert missing == ["workspace_root"]


def test_optional_input_never_missing():
    scenario = _scenario(InputSpec(key="workspace_root", label="W", required=False))
    missing = collect_missing_required_inputs(scenario, "p", None, None)
    assert missing == []


def test_multiple_required_inputs_collected_in_order():
    scenario = _scenario(
        InputSpec(key="prompt", label="P", required=True),
        InputSpec(key="workspace_root", label="W", required=True),
        InputSpec(key="project_context_file", label="C", required=True),
    )
    missing = collect_missing_required_inputs(scenario, "", None, "")
    assert missing == ["prompt", "workspace_root", "project_context_file"]
