
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
from backend.App.integrations.infrastructure.pipeline_presets import resolve_preset
from backend.App.orchestration.application.scenarios.resolution import ResolvedScenario


@dataclass
class ChatRequest:

    agent_config: dict[str, Any]
    pipeline_steps: Optional[list[str]]
    resolved_scenario: Optional[ResolvedScenario] = field(default=None)


class ChatRequestResolver:
    def __init__(
        self,
        *,
        merge_config=merge_agent_config,
        load_preset=resolve_preset,
    ) -> None:
        self._merge_config = merge_config
        self._load_preset = load_preset

    def resolve(self, request_data: Any) -> ChatRequest:
        raw_scenario_id = getattr(request_data, "scenario_id", None)
        scenario_id = raw_scenario_id.strip() if isinstance(raw_scenario_id, str) else None
        raw_pipeline_preset = getattr(request_data, "pipeline_preset", None)
        pipeline_preset = (
            raw_pipeline_preset.strip()
            if isinstance(raw_pipeline_preset, str)
            else None
        )
        raw_request_steps = getattr(request_data, "pipeline_steps", None)
        request_steps = raw_request_steps if isinstance(raw_request_steps, list) else None
        request_agent_config = getattr(request_data, "agent_config", None)
        request_workspace_write = getattr(request_data, "workspace_write", None)
        raw_scenario_overrides_map = getattr(request_data, "scenario_overrides", None)
        scenario_overrides_map = (
            raw_scenario_overrides_map
            if isinstance(raw_scenario_overrides_map, dict)
            else {}
        )

        if scenario_id and pipeline_preset:
            raise ValueError("Cannot use scenario_id together with pipeline_preset")

        if scenario_id:
            from backend.App.orchestration.application.scenarios.registry import default_scenario_registry
            from backend.App.orchestration.domain.scenarios.errors import ScenarioNotFound
            from backend.App.orchestration.application.scenarios.resolution import resolve_scenario_overrides

            try:
                scenario = default_scenario_registry().get(scenario_id)
            except ScenarioNotFound:
                raise ValueError(f"Unknown scenario_id: {scenario_id!r}")

            picked_override = scenario_overrides_map.get(scenario_id) if isinstance(
                scenario_overrides_map, dict
            ) else None
            skip_gates_raw = None
            model_profile_raw = None
            if isinstance(picked_override, dict):
                if isinstance(picked_override.get("skip_gates"), list):
                    skip_gates_raw = [
                        str(s) for s in picked_override["skip_gates"] if isinstance(s, str)
                    ]
                if isinstance(picked_override.get("model_profile"), dict):
                    model_profile_raw = {
                        str(role): str(model)
                        for role, model in picked_override["model_profile"].items()
                        if isinstance(role, str) and isinstance(model, str)
                    }

            resolved = resolve_scenario_overrides(
                scenario,
                request_steps,
                request_agent_config,
                request_workspace_write,
                skip_gates_raw,
                model_profile_raw,
            )
            agent_config = self._merge_config(resolved.agent_config)
            return ChatRequest(
                agent_config=agent_config,
                pipeline_steps=resolved.pipeline_steps,
                resolved_scenario=resolved,
            )

        agent_config = self._merge_config(request_agent_config)
        steps: Optional[list[str]] = request_steps
        if steps is None and pipeline_preset:
            steps = self._load_preset(pipeline_preset)
        return ChatRequest(agent_config=agent_config, pipeline_steps=steps)
