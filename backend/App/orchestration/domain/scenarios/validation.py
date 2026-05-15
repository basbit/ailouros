from __future__ import annotations

import re
from typing import Any

from backend.App.orchestration.domain.scenarios.errors import ScenarioInvalid
from backend.App.orchestration.domain.scenarios.inputs import (
    VALID_INPUT_KEYS,
    InputSpec,
)
from backend.App.orchestration.domain.scenarios.quality_checks import (
    VALID_CHECK_TYPES,
    VALID_SEVERITIES,
    QualityCheckSpec,
)
from backend.App.orchestration.domain.scenarios.scenario import Scenario

_VALID_CATEGORIES = frozenset({
    "development",
    "research",
    "code_quality",
    "content",
    "data",
    "product",
    "support",
    "visual_qa",
    "seo",
})

_REQUIRED_KEYS = ("id", "title", "category", "description", "pipeline_steps")

_CROLE_PREFIX = "crole_"
_CROLE_SLUG_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _is_custom_role_step(step_id: str) -> bool:
    if not step_id.startswith(_CROLE_PREFIX):
        return False
    slug = step_id[len(_CROLE_PREFIX):]
    return bool(slug and _CROLE_SLUG_RE.match(slug))


def validate_scenario_payload(
    payload: dict[str, Any],
    known_step_ids: frozenset[str],
) -> Scenario:

    for key in _REQUIRED_KEYS:
        if key not in payload:
            raise ScenarioInvalid(f"Missing required key: {key!r}")

    scenario_id = payload["id"]
    title = payload["title"]
    category = payload["category"]
    description = payload["description"]
    pipeline_steps_raw = payload["pipeline_steps"]

    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ScenarioInvalid("id must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ScenarioInvalid("title must be a non-empty string")
    if not isinstance(category, str):
        raise ScenarioInvalid("category must be a string")
    if category not in _VALID_CATEGORIES:
        raise ScenarioInvalid(
            f"category {category!r} is not valid; must be one of {sorted(_VALID_CATEGORIES)}"
        )
    if not isinstance(description, str) or not description.strip():
        raise ScenarioInvalid("description must be a non-empty string")
    if not isinstance(pipeline_steps_raw, list) or not pipeline_steps_raw:
        raise ScenarioInvalid("pipeline_steps must be a non-empty list")

    steps: list[str] = []
    for item in pipeline_steps_raw:
        if not isinstance(item, str) or not item.strip():
            raise ScenarioInvalid("Each pipeline_steps entry must be a non-empty string")
        steps.append(item.strip())

    unknown = [
        step for step in steps
        if step not in known_step_ids and not _is_custom_role_step(step)
    ]
    if unknown:
        raise ScenarioInvalid(f"Unknown step ids in pipeline_steps: {unknown}")

    seen: set[str] = set()
    for step in steps:
        if step in seen:
            raise ScenarioInvalid(f"Duplicate step {step!r} in pipeline_steps")
        seen.add(step)

    def _str_list(key: str) -> list[str]:
        raw = payload.get(key, [])
        if not isinstance(raw, list):
            raise ScenarioInvalid(f"{key!r} must be a list")
        result: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise ScenarioInvalid(f"Each entry in {key!r} must be a non-empty string")
            result.append(item.strip())
        return result

    default_gates = _str_list("default_gates")
    expected_artifacts = _str_list("expected_artifacts")
    required_tools = _str_list("required_tools")
    tags = _str_list("tags")

    for artifact_path in expected_artifacts:
        if artifact_path.startswith("/") or artifact_path.startswith("\\"):
            raise ScenarioInvalid(
                f"expected_artifacts entry must be a relative path, got {artifact_path!r}"
            )
        parts = artifact_path.replace("\\", "/").split("/")
        if any(part == ".." for part in parts):
            raise ScenarioInvalid(
                f"expected_artifacts entry must not contain '..' segments, got {artifact_path!r}"
            )

    workspace_write_default = payload.get("workspace_write_default", False)
    if not isinstance(workspace_write_default, bool):
        raise ScenarioInvalid("workspace_write_default must be a bool")

    recommended_models = payload.get("recommended_models", {})
    if not isinstance(recommended_models, dict):
        raise ScenarioInvalid("recommended_models must be a dict")
    for role, model in recommended_models.items():
        if not isinstance(model, str):
            raise ScenarioInvalid(
                f"recommended_models values must be strings; "
                f"got {type(model).__name__!r} for key {role!r}"
            )

    agent_config_defaults = payload.get("agent_config_defaults", {})
    if not isinstance(agent_config_defaults, dict):
        raise ScenarioInvalid("agent_config_defaults must be a dict")

    quality_checks = _parse_quality_checks(payload.get("quality_checks", []))
    inputs = _parse_inputs(payload.get("inputs", []))

    step_estimates_raw = payload.get("step_estimates", [])
    if not isinstance(step_estimates_raw, list):
        raise ScenarioInvalid("step_estimates must be a list when provided")
    step_estimates: list[dict[str, Any]] = []
    for entry in step_estimates_raw:
        if not isinstance(entry, dict):
            raise ScenarioInvalid("step_estimates entry must be an object")
        sid = entry.get("step_id") or entry.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ScenarioInvalid("step_estimates entry must have non-empty step_id")
        step_estimates.append(dict(entry))

    return Scenario(
        id=scenario_id.strip(),
        title=title.strip(),
        category=category,
        description=description.strip(),
        pipeline_steps=tuple(steps),
        default_gates=tuple(default_gates),
        expected_artifacts=tuple(expected_artifacts),
        required_tools=tuple(required_tools),
        workspace_write_default=workspace_write_default,
        recommended_models=dict(recommended_models),
        agent_config_defaults=dict(agent_config_defaults),
        tags=tuple(tags),
        quality_checks=quality_checks,
        inputs=inputs,
        step_estimates=tuple(step_estimates),
    )


def _parse_inputs(raw: Any) -> tuple[InputSpec, ...]:
    if not isinstance(raw, list):
        raise ScenarioInvalid("inputs must be a list")
    parsed: list[InputSpec] = []
    seen_keys: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ScenarioInvalid("Each inputs entry must be a JSON object")
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ScenarioInvalid("inputs entry must have a non-empty 'key' string")
        key_clean = key.strip()
        if key_clean not in VALID_INPUT_KEYS:
            raise ScenarioInvalid(
                f"inputs entry {key_clean!r}: 'key' must be one of "
                f"{sorted(VALID_INPUT_KEYS)}"
            )
        if key_clean in seen_keys:
            raise ScenarioInvalid(f"Duplicate input key {key_clean!r}")
        seen_keys.add(key_clean)
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ScenarioInvalid(
                f"inputs entry {key_clean!r}: 'label' must be a non-empty string"
            )
        hint = entry.get("hint", "")
        if not isinstance(hint, str):
            raise ScenarioInvalid(
                f"inputs entry {key_clean!r}: 'hint' must be a string"
            )
        required = entry.get("required", False)
        if not isinstance(required, bool):
            raise ScenarioInvalid(
                f"inputs entry {key_clean!r}: 'required' must be a bool"
            )
        parsed.append(
            InputSpec(
                key=key_clean,
                label=label.strip(),
                hint=hint.strip(),
                required=required,
            )
        )
    return tuple(parsed)


def _parse_quality_checks(raw: Any) -> tuple[QualityCheckSpec, ...]:
    if not isinstance(raw, list):
        raise ScenarioInvalid("quality_checks must be a list")
    parsed: list[QualityCheckSpec] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ScenarioInvalid("Each quality_checks entry must be a JSON object")
        check_id = entry.get("id")
        check_type = entry.get("type")
        if not isinstance(check_id, str) or not check_id.strip():
            raise ScenarioInvalid("quality_checks entry must have a non-empty 'id' string")
        if not isinstance(check_type, str) or check_type not in VALID_CHECK_TYPES:
            raise ScenarioInvalid(
                f"quality_checks entry {check_id!r}: 'type' must be one of "
                f"{sorted(VALID_CHECK_TYPES)}, got {check_type!r}"
            )
        if check_id in seen_ids:
            raise ScenarioInvalid(f"Duplicate quality_check id {check_id!r}")
        seen_ids.add(check_id)
        severity = entry.get("severity", "error")
        if severity not in VALID_SEVERITIES:
            raise ScenarioInvalid(
                f"quality_checks entry {check_id!r}: 'severity' must be one of "
                f"{sorted(VALID_SEVERITIES)}, got {severity!r}"
            )
        blocking = entry.get("blocking", False)
        if not isinstance(blocking, bool):
            raise ScenarioInvalid(
                f"quality_checks entry {check_id!r}: 'blocking' must be a bool"
            )
        config = entry.get("config", {})
        if not isinstance(config, dict):
            raise ScenarioInvalid(
                f"quality_checks entry {check_id!r}: 'config' must be a dict"
            )
        parsed.append(
            QualityCheckSpec(
                id=check_id.strip(),
                type=check_type,
                severity=severity,
                blocking=blocking,
                config=dict(config),
            )
        )
    return tuple(parsed)
