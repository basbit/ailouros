from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.App.integrations.application.rest_misc_service import (
    defaults_payload,
    health_payload,
    pipeline_plan_payload,
    prometheus_metrics_response_or_none,
    prompts_list_payload,
    skills_list_payload,
    system_update_status,
    workspace_files_payload,
)
from backend.App.shared.health.health_service import (
    aggregate_status,
    run_all_probes,
)
from backend.App.shared.health.registry import default_probes
from backend.UI.REST.schemas import PipelinePlanRequest

router = APIRouter()


def _system_health_payload() -> dict[str, Any]:
    probes = default_probes()
    results = run_all_probes(probes)
    return {
        "status": aggregate_status(results),
        "subsystems": [r.to_payload() for r in results],
    }


@router.get("/v1/health")
async def system_health() -> JSONResponse:
    return JSONResponse(content=_system_health_payload())


@router.get("/v1/health/{subsystem}")
async def system_health_subsystem(subsystem: str) -> JSONResponse:
    matches = tuple(p for p in default_probes() if p.subsystem == subsystem)
    if not matches:
        raise HTTPException(status_code=404, detail=f"unknown subsystem: {subsystem}")
    results = run_all_probes(matches)
    if not results:
        raise HTTPException(status_code=500, detail="probe produced no result")
    return JSONResponse(content=results[0].to_payload())


@router.get("/v1/system/update-available")
async def system_update_available() -> JSONResponse:
    return JSONResponse(system_update_status())


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    from backend.App.shared.infrastructure.bootstrap.task_store_factory import (
        get_task_store,
    )

    payload, status_code = await health_payload(get_task_store())
    return JSONResponse(content=payload, status_code=status_code)


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    response = prometheus_metrics_response_or_none()
    if response is None:
        raise HTTPException(
            status_code=404,
            detail="Prometheus export off (SWARM_PROMETHEUS=0) or prometheus-client missing",
        )
    return response


@router.get("/v1/defaults")
async def get_defaults() -> JSONResponse:
    return JSONResponse(content=defaults_payload())


@router.post("/v1/pipeline/plan")
async def pipeline_plan(body: PipelinePlanRequest) -> Any:
    payload = await pipeline_plan_payload(
        goal=body.goal,
        agent_config=body.agent_config,
        constraints=body.constraints,
    )
    return JSONResponse(content=payload)


@router.get("/v1/workspace/files")
async def get_workspace_files(workspace_root: str = Query("")) -> JSONResponse:
    payload, status_code = workspace_files_payload(workspace_root)
    return JSONResponse(payload, status_code=status_code)


@router.get("/v1/prompts/list")
async def get_prompts_list() -> JSONResponse:
    return JSONResponse(content=prompts_list_payload())


@router.get("/v1/skills/list")
async def get_skills_list(workspace_root: str = Query("")) -> JSONResponse:
    payload, status_code = skills_list_payload(workspace_root)
    return JSONResponse(payload, status_code=status_code)


@router.get("/live")
async def liveness() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@router.get("/ready")
async def readiness() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
