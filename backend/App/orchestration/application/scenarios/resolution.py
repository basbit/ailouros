from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.orchestration.domain.scenarios.quality_checks import QualityCheckSpec
from backend.App.orchestration.domain.scenarios.scenario import Scenario


@dataclass(frozen=True)
class ResolvedScenario:
    scenario_id: str
    scenario_title: str
    scenario_category: str
    pipeline_steps: list[str]
    agent_config: dict[str, Any]
    expected_artifacts: tuple[str, ...]
    default_gates: tuple[str, ...]
    required_tools: tuple[str, ...]
    workspace_write: bool
    warnings: list[str]
    quality_checks: tuple[QualityCheckSpec, ...] = ()
    skipped_gates: tuple[str, ...] = ()
    model_profile_applied: dict[str, str] = field(default_factory=dict)


def check_required_tools(
    scenario: Scenario,
    agent_config: dict[str, Any],
    workspace_write: bool,
) -> list[str]:
    warnings: list[str] = []
    for tool in scenario.required_tools:
        if tool == "web_search":
            swarm = agent_config.get("swarm") or {}
            has_key = (
                bool(str(swarm.get("tavily_api_key") or "").strip())
                or bool(str(swarm.get("exa_api_key") or "").strip())
                or bool(str(swarm.get("scrapingdog_api_key") or "").strip())
            )
            if not has_key:
                warnings.append(f"Required tool {tool!r} is not configured.")
        elif tool == "workspace_write":
            if not workspace_write:
                warnings.append(f"Required tool {tool!r} is not configured.")
        elif tool == "mcp_filesystem":
            mcp = agent_config.get("mcp") or {}
            servers = mcp.get("servers") if isinstance(mcp, dict) else None
            if not servers:
                warnings.append(f"Required tool {tool!r} is not configured.")
        else:
            warnings.append(f"Required tool {tool!r} is not recognized.")
    return warnings


def _deep_merge_agent_config(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            merged = copy.deepcopy(result[key])
            merged.update(copy.deepcopy(value))
            result[key] = merged
        else:
            result[key] = copy.deepcopy(value)
    return result


def _apply_skip_gates(
    steps: list[str],
    default_gates: tuple[str, ...],
    skip_gates: list[str],
) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    skip_clean = [s.strip() for s in skip_gates if isinstance(s, str) and s.strip()]
    if not skip_clean:
        return steps, default_gates, ()
    valid_targets = set(default_gates)
    actually_skipped = [s for s in skip_clean if s in valid_targets]
    if not actually_skipped:
        return steps, default_gates, ()
    skipped_set = set(actually_skipped)
    new_steps = [s for s in steps if s not in skipped_set]
    new_gates = tuple(g for g in default_gates if g not in skipped_set)
    return new_steps, new_gates, tuple(actually_skipped)


def _apply_model_profile(
    agent_config: dict[str, Any],
    model_profile: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    if not model_profile:
        return agent_config, {}
    applied: dict[str, str] = {}
    out = copy.deepcopy(agent_config)
    for role, model in model_profile.items():
        if not isinstance(role, str) or not isinstance(model, str):
            continue
        role_clean = role.strip()
        model_clean = model.strip()
        if not role_clean or not model_clean:
            continue
        existing = out.get(role_clean)
        if isinstance(existing, dict):
            merged = copy.deepcopy(existing)
            merged["model"] = model_clean
            out[role_clean] = merged
        else:
            out[role_clean] = {"model": model_clean}
        applied[role_clean] = model_clean
    return out, applied


def resolve_scenario_overrides(
    scenario: Scenario,
    request_pipeline_steps: Optional[list[str]],
    request_agent_config: Optional[dict[str, Any]],
    request_workspace_write: Optional[bool],
    request_skip_gates: Optional[list[str]] = None,
    request_model_profile: Optional[dict[str, str]] = None,
) -> ResolvedScenario:
    if request_pipeline_steps:
        steps = list(request_pipeline_steps)
    else:
        steps = list(scenario.pipeline_steps)

    agent_config = _deep_merge_agent_config(
        scenario.agent_config_defaults,
        request_agent_config or {},
    )

    if request_workspace_write is not None:
        workspace_write = request_workspace_write
    else:
        workspace_write = scenario.workspace_write_default

    steps, effective_gates, skipped = _apply_skip_gates(
        steps, scenario.default_gates, request_skip_gates or [],
    )

    agent_config, profile_applied = _apply_model_profile(
        agent_config, request_model_profile or {},
    )

    warnings = check_required_tools(scenario, agent_config, workspace_write)

    return ResolvedScenario(
        scenario_id=scenario.id,
        scenario_title=scenario.title,
        scenario_category=scenario.category,
        pipeline_steps=steps,
        agent_config=agent_config,
        expected_artifacts=scenario.expected_artifacts,
        default_gates=effective_gates,
        required_tools=scenario.required_tools,
        workspace_write=workspace_write,
        warnings=warnings,
        quality_checks=scenario.quality_checks,
        skipped_gates=skipped,
        model_profile_applied=profile_applied,
    )
