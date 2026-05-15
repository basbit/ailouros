from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.App.orchestration.application.scenarios.preview import (
    PreviewOverrides,
    build_scenario_preview,
)
from backend.App.orchestration.application.scenarios.registry import default_scenario_registry
from backend.App.orchestration.domain.scenarios.errors import (
    ScenarioInvalid,
    ScenarioNotFound,
)
from backend.UI.REST.schemas import ScenarioPreviewRequest

router = APIRouter()


@router.get("/v1/scenarios")
def list_scenarios() -> dict[str, Any]:
    registry = default_scenario_registry()
    scenarios = sorted(
        registry.list_all(),
        key=lambda scenario: (scenario.category, scenario.title),
    )
    return {"version": 1, "scenarios": [scenario.summary() for scenario in scenarios]}


@router.get("/v1/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> dict[str, Any]:
    try:
        scenario = default_scenario_registry().get(scenario_id)
    except ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": scenario.id,
        "title": scenario.title,
        "category": scenario.category,
        "description": scenario.description,
        "pipeline_steps": list(scenario.pipeline_steps),
        "default_gates": list(scenario.default_gates),
        "expected_artifacts": list(scenario.expected_artifacts),
        "required_tools": list(scenario.required_tools),
        "recommended_models": dict(scenario.recommended_models),
        "workspace_write_default": scenario.workspace_write_default,
        "agent_config_defaults": dict(scenario.agent_config_defaults),
        "tags": list(scenario.tags),
    }


@router.post("/v1/scenarios/preview")
def preview_scenario(body: ScenarioPreviewRequest) -> dict[str, Any]:
    try:
        scenario = default_scenario_registry().get(body.scenario_id)
    except ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        overrides = PreviewOverrides(
            pipeline_steps=body.pipeline_steps,
            agent_config=body.agent_config,
            workspace_write=body.workspace_write,
            skip_gates=body.skip_gates,
            model_profile=body.model_profile,
        )
        return build_scenario_preview(scenario, overrides)
    except ScenarioInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/scenarios/{scenario_id}/estimate")
def estimate_scenario(scenario_id: str) -> dict[str, Any]:
    from backend.App.orchestration.domain.scenario_estimate import compute_scenario_estimate

    try:
        scenario = default_scenario_registry().get(scenario_id)
    except ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload: dict[str, Any] = {
        "id": scenario.id,
        "pipeline_steps": list(scenario.pipeline_steps),
    }
    if scenario.step_estimates:
        payload["step_estimates"] = [dict(entry) for entry in scenario.step_estimates]
    estimate = compute_scenario_estimate(payload)
    return estimate.to_dict()


@router.post("/v1/scenarios/validate")
def validate_scenario(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.App.orchestration.application.routing.step_registry import (
        PIPELINE_STEP_REGISTRY,
    )
    from backend.App.orchestration.domain.scenarios.validation import (
        validate_scenario_payload,
    )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422, detail="payload must be a JSON object"
        )
    try:
        scenario = validate_scenario_payload(
            payload, frozenset(PIPELINE_STEP_REGISTRY.keys())
        )
    except ScenarioInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"valid": True, "id": scenario.id, "summary": scenario.summary()}
