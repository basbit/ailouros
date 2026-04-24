from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.App.integrations.application.rest_misc_service import (
    defaults_payload,
    health_payload,
    observability_metrics_payload,
    pipeline_plan_payload,
    prometheus_metrics_response_or_none,
    prompts_list_payload,
    skills_list_payload,
    system_update_status,
    workspace_files_payload,
)
from backend.UI.REST.schemas import PipelinePlanRequest

router = APIRouter()


@router.get("/v1/system/update-available")
async def system_update_available() -> JSONResponse:
    return JSONResponse(system_update_status())


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    from backend.App.tasks.application.task_runtime import task_store

    payload, status_code = await health_payload(task_store)
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


@router.get("/v1/observability/metrics")
async def observability_metrics() -> Any:
    return JSONResponse(content=observability_metrics_payload())


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


@router.get("/v1/mcp/status")
def get_mcp_status(request: Request) -> JSONResponse:
    manager = getattr(request.app.state, "mcp_manager", None)
    if not manager:
        return JSONResponse(content={"servers": {}, "autostart_enabled": False})
    return JSONResponse(
        content={"servers": manager.get_status(), "autostart_enabled": True}
    )


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
