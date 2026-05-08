from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.orchestration.domain.scenarios.scenario import Scenario
from backend.App.orchestration.application.scenarios.resolution import resolve_scenario_overrides


@dataclass(frozen=True)
class PreviewOverrides:
    pipeline_steps: Optional[list[str]] = None
    agent_config: Optional[dict[str, Any]] = None
    workspace_write: Optional[bool] = None
    skip_gates: Optional[list[str]] = None
    model_profile: Optional[dict[str, str]] = field(default=None)


def build_scenario_preview(
    scenario: Scenario,
    overrides: PreviewOverrides,
) -> dict[str, Any]:
    resolved = resolve_scenario_overrides(
        scenario,
        overrides.pipeline_steps,
        overrides.agent_config,
        overrides.workspace_write,
        overrides.skip_gates,
        overrides.model_profile,
    )
    return {
        "scenario": scenario.summary(),
        "pipeline_steps": resolved.pipeline_steps,
        "default_gates": list(resolved.default_gates),
        "expected_artifacts": list(resolved.expected_artifacts),
        "required_tools": list(resolved.required_tools),
        "recommended_models": dict(scenario.recommended_models),
        "agent_config": resolved.agent_config,
        "workspace_write": resolved.workspace_write,
        "warnings": resolved.warnings,
        "skipped_gates": list(resolved.skipped_gates),
        "model_profile_applied": dict(resolved.model_profile_applied),
    }
