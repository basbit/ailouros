"""Тесты для validate_scenario_payload."""

import pytest

from backend.App.orchestration.domain.scenarios.errors import ScenarioInvalid
from backend.App.orchestration.domain.scenarios.validation import validate_scenario_payload
from backend.App.orchestration.domain.scenarios.scenario import Scenario


_KNOWN = frozenset(["clarify_input", "pm", "ba", "architect", "dev", "qa"])


def _base() -> dict:
    return {
        "id": "test_scenario",
        "title": "Test Scenario",
        "category": "development",
        "description": "A test scenario.",
        "pipeline_steps": ["clarify_input", "pm"],
        "default_gates": [],
        "expected_artifacts": [],
        "required_tools": [],
        "workspace_write_default": False,
        "recommended_models": {},
        "agent_config_defaults": {},
        "tags": [],
    }


def test_valid_payload_returns_scenario():
    result = validate_scenario_payload(_base(), _KNOWN)
    assert isinstance(result, Scenario)
    assert result.id == "test_scenario"
    assert result.pipeline_steps == ("clarify_input", "pm")


def test_missing_required_key_raises():
    payload = _base()
    del payload["title"]
    with pytest.raises(ScenarioInvalid, match="title"):
        validate_scenario_payload(payload, _KNOWN)


def test_missing_id_raises():
    payload = _base()
    del payload["id"]
    with pytest.raises(ScenarioInvalid, match="id"):
        validate_scenario_payload(payload, _KNOWN)


def test_empty_pipeline_steps_raises():
    payload = _base()
    payload["pipeline_steps"] = []
    with pytest.raises(ScenarioInvalid, match="pipeline_steps"):
        validate_scenario_payload(payload, _KNOWN)


def test_duplicate_steps_raise():
    payload = _base()
    payload["pipeline_steps"] = ["clarify_input", "clarify_input"]
    with pytest.raises(ScenarioInvalid, match="Duplicate"):
        validate_scenario_payload(payload, _KNOWN)


def test_unknown_step_id_raises_with_ids():
    payload = _base()
    payload["pipeline_steps"] = ["clarify_input", "nonexistent_step"]
    with pytest.raises(ScenarioInvalid) as exc_info:
        validate_scenario_payload(payload, _KNOWN)
    assert "nonexistent_step" in str(exc_info.value)


def test_unknown_category_raises():
    payload = _base()
    payload["category"] = "invalid_category"
    with pytest.raises(ScenarioInvalid, match="category"):
        validate_scenario_payload(payload, _KNOWN)


def test_crole_slugs_accepted():
    payload = _base()
    payload["pipeline_steps"] = ["clarify_input", "crole_my_role"]
    result = validate_scenario_payload(payload, _KNOWN)
    assert "crole_my_role" in result.pipeline_steps


def test_expected_artifacts_rejects_absolute_path():
    payload = _base()
    payload["expected_artifacts"] = ["/etc/passwd"]
    with pytest.raises(ScenarioInvalid, match="relative path"):
        validate_scenario_payload(payload, _KNOWN)


def test_expected_artifacts_rejects_parent_traversal():
    payload = _base()
    payload["expected_artifacts"] = ["../secret.txt"]
    with pytest.raises(ScenarioInvalid, match=r"\.\."):
        validate_scenario_payload(payload, _KNOWN)


def test_expected_artifacts_rejects_nested_traversal():
    payload = _base()
    payload["expected_artifacts"] = ["agents/../../escape.txt"]
    with pytest.raises(ScenarioInvalid, match=r"\.\."):
        validate_scenario_payload(payload, _KNOWN)


def test_quality_checks_accepts_valid_specs():
    payload = _base()
    payload["quality_checks"] = [
        {"id": "min_present", "type": "artifact_count", "config": {"min": 2}},
        {
            "id": "qa_passes",
            "type": "agent_output_contains",
            "severity": "warning",
            "blocking": False,
            "config": {"agent": "qa", "substring": "passed"},
        },
    ]
    result = validate_scenario_payload(payload, _KNOWN)
    assert len(result.quality_checks) == 2
    assert result.quality_checks[0].id == "min_present"
    assert result.quality_checks[1].severity == "warning"


def test_quality_checks_rejects_unknown_type():
    payload = _base()
    payload["quality_checks"] = [{"id": "x", "type": "unknown_type"}]
    with pytest.raises(ScenarioInvalid, match="type"):
        validate_scenario_payload(payload, _KNOWN)


def test_quality_checks_rejects_duplicate_ids():
    payload = _base()
    payload["quality_checks"] = [
        {"id": "x", "type": "artifact_count"},
        {"id": "x", "type": "no_warnings"},
    ]
    with pytest.raises(ScenarioInvalid, match="Duplicate"):
        validate_scenario_payload(payload, _KNOWN)


def test_quality_checks_rejects_unknown_severity():
    payload = _base()
    payload["quality_checks"] = [
        {"id": "x", "type": "artifact_count", "severity": "fatal"},
    ]
    with pytest.raises(ScenarioInvalid, match="severity"):
        validate_scenario_payload(payload, _KNOWN)


def test_quality_checks_blocking_must_be_bool():
    payload = _base()
    payload["quality_checks"] = [
        {"id": "x", "type": "artifact_count", "blocking": "yes"},
    ]
    with pytest.raises(ScenarioInvalid, match="blocking"):
        validate_scenario_payload(payload, _KNOWN)


def test_quality_checks_default_to_empty():
    payload = _base()
    result = validate_scenario_payload(payload, _KNOWN)
    assert result.quality_checks == ()


def test_inputs_accepts_valid_specs():
    payload = _base()
    payload["inputs"] = [
        {"key": "prompt", "label": "Task", "required": True},
        {
            "key": "workspace_root",
            "label": "Workspace",
            "hint": "Path to repo",
            "required": True,
        },
        {"key": "workspace_write", "label": "Write toggle"},
    ]
    result = validate_scenario_payload(payload, _KNOWN)
    assert len(result.inputs) == 3
    assert result.inputs[0].key == "prompt"
    assert result.inputs[0].required is True
    assert result.inputs[1].hint == "Path to repo"
    assert result.inputs[2].required is False


def test_inputs_rejects_unknown_key():
    payload = _base()
    payload["inputs"] = [{"key": "unknown_field", "label": "X"}]
    with pytest.raises(ScenarioInvalid, match="key"):
        validate_scenario_payload(payload, _KNOWN)


def test_inputs_rejects_duplicate_key():
    payload = _base()
    payload["inputs"] = [
        {"key": "prompt", "label": "A"},
        {"key": "prompt", "label": "B"},
    ]
    with pytest.raises(ScenarioInvalid, match="Duplicate"):
        validate_scenario_payload(payload, _KNOWN)


def test_inputs_rejects_missing_label():
    payload = _base()
    payload["inputs"] = [{"key": "prompt"}]
    with pytest.raises(ScenarioInvalid, match="label"):
        validate_scenario_payload(payload, _KNOWN)


def test_inputs_rejects_non_bool_required():
    payload = _base()
    payload["inputs"] = [{"key": "prompt", "label": "X", "required": "yes"}]
    with pytest.raises(ScenarioInvalid, match="required"):
        validate_scenario_payload(payload, _KNOWN)


def test_inputs_default_to_empty():
    payload = _base()
    result = validate_scenario_payload(payload, _KNOWN)
    assert result.inputs == ()
