"""Onboarding routes: /v1/onboarding/* workspace scan, apply, preconfigure, MCP config."""

from __future__ import annotations

import asyncio
import dataclasses
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.UI.REST.schemas import (
    OnboardingApplyRequest,
    OnboardingMcpApplyRequest,
    OnboardingMcpPreflightRequest,
    OnboardingPreconfigureRequest,
)

router = APIRouter()


@router.get("/v1/onboarding/scan")
async def onboarding_scan(workspace_root: str = "") -> JSONResponse:
    """Scan workspace and propose initial config. Does NOT write anything."""
    from backend.App.integrations.application.onboarding_service import scan_workspace
    result = await asyncio.to_thread(scan_workspace, workspace_root)
    return JSONResponse(content=result.to_dict())


@router.post("/v1/onboarding/apply")
async def onboarding_apply(body: OnboardingApplyRequest) -> JSONResponse:
    """Apply the proposed .swarm/context.txt after explicit user confirmation."""
    from backend.App.integrations.application.onboarding_service import apply_onboarding_config
    try:
        await asyncio.to_thread(apply_onboarding_config, body.workspace_root, body.content)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "ok", "workspace_root": body.workspace_root})


@router.post("/v1/onboarding/preconfigure")
async def onboarding_preconfigure(body: OnboardingPreconfigureRequest) -> JSONResponse:
    """Run AI pre-configure: analyze project and recommend MCP servers."""
    from backend.App.integrations.application.onboarding_service import run_ai_preconfigure
    from backend.App.integrations.infrastructure.mcp.auto.setup import recommend_mcp_servers, build_mcp_config

    result = await asyncio.to_thread(
        run_ai_preconfigure, body.workspace_root, body.base_model or ""
    )
    from backend.App.integrations.application.onboarding_service import _detect_stack
    from pathlib import Path as _Path

    detected_stack: list[str] = []
    if body.workspace_root:
        root = _Path(body.workspace_root)
        if root.exists():
            detected_stack = _detect_stack(root)

    brave_key = os.getenv("SWARM_BRAVE_SEARCH_API_KEY", "") or os.getenv("BRAVE_API_KEY", "")
    specs = recommend_mcp_servers(body.workspace_root or "", detected_stack, brave_api_key=brave_key)
    if result.mcp_recommendations:
        rec_set = set(result.mcp_recommendations)
        filtered = [s for s in specs if s.name in rec_set]
        base_names = set(os.getenv("SWARM_MCP_BASE_SERVERS", "filesystem,git,fetch").split(","))
        for s in specs:
            if s.name in base_names and s.name not in {f.name for f in filtered}:
                filtered.insert(0, s)
        specs = filtered if filtered else specs

    mcp_config_preview = build_mcp_config(
        specs,
        body.workspace_root or "",
        generated_by="ai_preconfigure",
        base_model=result.base_model,
    )
    return JSONResponse(content={
        "mcp_recommendations": [dataclasses.asdict(s) for s in specs],
        "context_mode": result.context_mode,
        "priority_paths": result.priority_paths,
        "raw_response": result.raw_response,
        "error": result.error,
        "base_model": result.base_model,
        "mcp_config_preview": mcp_config_preview,
        "detected_stack": detected_stack,
    })


@router.get("/v1/onboarding/mcp-config")
async def onboarding_get_mcp_config(workspace_root: str = "") -> JSONResponse:
    """Return current .swarm/mcp_config.json for the workspace."""
    from backend.App.integrations.infrastructure.mcp.auto.setup import load_mcp_config
    config = await asyncio.to_thread(load_mcp_config, workspace_root)
    if config is None:
        return JSONResponse(content={"exists": False, "config": None})
    return JSONResponse(content={"exists": True, "config": config})


@router.post("/v1/onboarding/mcp-config/apply")
async def onboarding_apply_mcp_config(body: OnboardingMcpApplyRequest) -> JSONResponse:
    """Save proposed MCP config after explicit user Apply."""
    from backend.App.integrations.infrastructure.mcp.auto.setup import (
        save_mcp_config,
        build_mcp_config,
        MCPServerSpec,
    )

    if body.config is not None:
        cfg = body.config
    elif body.servers is not None:
        specs = [
            MCPServerSpec(
                name=s.name,
                package="",
                transport=s.transport,
                command=s.command,
                args=s.args,
                reason=s.reason,
                enabled=s.enabled,
            )
            for s in body.servers
        ]
        cfg = build_mcp_config(specs, body.workspace_root)
    else:
        raise HTTPException(
            status_code=422,
            detail="Request must include either 'servers' or 'config' field",
        )

    try:
        path = await asyncio.to_thread(save_mcp_config, body.workspace_root, cfg)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "ok", "path": str(path)})


@router.post("/v1/onboarding/mcp-preflight")
async def onboarding_mcp_preflight(body: OnboardingMcpPreflightRequest) -> JSONResponse:
    """Run preflight check for each MCP server in the workspace config."""
    from backend.App.integrations.infrastructure.mcp.auto.setup import load_mcp_config
    from backend.App.integrations.infrastructure.mcp.stdio.mcp_pool import mcp_preflight_check
    from backend.App.orchestration.application.lifecycle_hooks import build_preflight_recommendations

    workspace_root = body.workspace_root or ""
    config = await asyncio.to_thread(load_mcp_config, workspace_root)
    if config is None:
        raise HTTPException(status_code=404, detail="No mcp_config.json found for workspace")

    servers_out: dict[str, dict] = {}
    for server in config.get("servers", []):
        name = server.get("name", "")
        if not name:
            continue
        enabled = server.get("enabled", True)
        if not enabled:
            servers_out[name] = {"status": "failed", "error": "server disabled"}
            continue
        servers_out[name] = await asyncio.to_thread(mcp_preflight_check, server)

    recommendations = build_preflight_recommendations(
        workspace_root,
        "retrieve_mcp",
        mcp_config=config,
    )
    return JSONResponse(content={"servers": servers_out, **recommendations})


@router.get("/v1/onboarding/models")
async def get_onboarding_models(workspace_root: str = Query("")) -> JSONResponse:
    """Discover available LLM models and return per-role assignments."""
    from backend.App.integrations.infrastructure.model_discovery import (
        discover_all_models,
        assign_models_to_roles,
        load_models_config,
    )

    if workspace_root:
        saved = await asyncio.to_thread(load_models_config, workspace_root)
        if saved:
            return JSONResponse(content={"source": "saved", "config": saved})

    models = await asyncio.to_thread(discover_all_models)
    assignments = await asyncio.to_thread(assign_models_to_roles, models)
    return JSONResponse(content={
        "source": "live",
        "discovered": [{"model_id": m.model_id, "provider": m.provider} for m in models],
        "assignments": [
            {
                "role": a.role,
                "model_id": a.model_id,
                "provider": a.provider,
                "reason": a.reason,
            }
            for a in assignments
        ],
    })
