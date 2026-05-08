from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.App.orchestration.domain.scenarios.errors import ScenarioInvalid
from backend.App.orchestration.domain.scenarios.inputs import InputSpec
from backend.App.orchestration.domain.scenarios.quality_checks import QualityCheckSpec


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    description: str
    pipeline_steps: tuple[str, ...]
    default_gates: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    required_tools: tuple[str, ...]
    workspace_write_default: bool
    recommended_models: dict[str, str]
    agent_config_defaults: dict[str, Any]
    tags: tuple[str, ...]
    quality_checks: tuple[QualityCheckSpec, ...] = field(default_factory=tuple)
    inputs: tuple[InputSpec, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ScenarioInvalid("Scenario id must be a non-empty string")
        if not self.title or not self.title.strip():
            raise ScenarioInvalid("Scenario title must be a non-empty string")
        if not self.pipeline_steps:
            raise ScenarioInvalid("Scenario pipeline_steps must be non-empty")
        seen: set[str] = set()
        for step in self.pipeline_steps:
            if step in seen:
                raise ScenarioInvalid(f"Duplicate step {step!r} in scenario {self.id!r}")
            seen.add(step)
        check_ids: set[str] = set()
        for check in self.quality_checks:
            if check.id in check_ids:
                raise ScenarioInvalid(
                    f"Duplicate quality_check id {check.id!r} in scenario {self.id!r}"
                )
            check_ids.add(check.id)
        input_keys: set[str] = set()
        for spec in self.inputs:
            if spec.key in input_keys:
                raise ScenarioInvalid(
                    f"Duplicate input key {spec.key!r} in scenario {self.id!r}"
                )
            input_keys.add(spec.key)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "pipeline_steps": list(self.pipeline_steps),
            "default_gates": list(self.default_gates),
            "expected_artifacts": list(self.expected_artifacts),
            "required_tools": list(self.required_tools),
            "workspace_write_default": self.workspace_write_default,
            "recommended_models": dict(self.recommended_models),
            "tags": list(self.tags),
            "quality_checks": [check.to_dict() for check in self.quality_checks],
            "inputs": [spec.to_dict() for spec in self.inputs],
        }
