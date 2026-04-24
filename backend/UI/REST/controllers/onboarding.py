from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.App.integrations.application.onboarding_service import (
    apply_mcp_config_payload,
    apply_onboarding_config,
    mcp_preflight_payload,
    onboarding_models_payload,
    preconfigure_payload,
    scan_workspace,
)
from backend.UI.REST.schemas import (
    OnboardingApplyRequest,
    OnboardingMcpApplyRequest,
    OnboardingMcpPreflightRequest,
    OnboardingPreconfigureRequest,
)

router = APIRouter()


@router.get("/v1/onboarding/scan")
async def onboarding_scan(workspace_root: str = "") -> JSONResponse:
    result = await asyncio.to_thread(scan_workspace, workspace_root)
    return JSONResponse(content=result.to_dict())


@router.post("/v1/onboarding/apply")
async def onboarding_apply(body: OnboardingApplyRequest) -> JSONResponse:
    try:
        await asyncio.to_thread(
            apply_onboarding_config, body.workspace_root, body.content
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "ok", "workspace_root": body.workspace_root})


@router.post("/v1/onboarding/preconfigure")
async def onboarding_preconfigure(body: OnboardingPreconfigureRequest) -> JSONResponse:
    payload = await asyncio.to_thread(
        preconfigure_payload,
        body.workspace_root,
        body.base_model or "",
    )
    return JSONResponse(content=payload)


@router.post("/v1/onboarding/mcp-config/apply")
async def onboarding_apply_mcp_config(body: OnboardingMcpApplyRequest) -> JSONResponse:
    servers = None
    if body.servers is not None:
        servers = [
            server.model_dump() if hasattr(server, "model_dump") else server.dict()
            for server in body.servers
        ]
    try:
        payload = await asyncio.to_thread(
            apply_mcp_config_payload,
            workspace_root=body.workspace_root,
            config=body.config,
            servers=servers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content=payload)


@router.post("/v1/onboarding/mcp-preflight")
async def onboarding_mcp_preflight(body: OnboardingMcpPreflightRequest) -> JSONResponse:
    try:
        payload = await asyncio.to_thread(
            mcp_preflight_payload,
            workspace_root=body.workspace_root or "",
            tavily_api_key=body.tavily_api_key,
            exa_api_key=body.exa_api_key,
            scrapingdog_api_key=body.scrapingdog_api_key,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(content=payload)


@router.get("/v1/onboarding/models")
async def get_onboarding_models(workspace_root: str = Query("")) -> JSONResponse:
    payload = await asyncio.to_thread(onboarding_models_payload, workspace_root)
    return JSONResponse(content=payload)
