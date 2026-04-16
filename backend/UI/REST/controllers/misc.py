"""Core utility routes: /health, /metrics, /v1/defaults, /v1/pipeline/plan,
/v1/mcp/*, /v1/circuit-breakers, /v1/failure-types, /v1/workspace/files.

Onboarding routes → controllers/onboarding.py
Memory routes     → controllers/memory.py
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from backend.App.integrations.infrastructure.observability.prometheus import prometheus_metrics_response
from backend.App.integrations.infrastructure.observability.step_metrics import snapshot as step_metrics_snapshot
from backend.UI.REST.schemas import PipelinePlanRequest

router = APIRouter()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/v1/system/update-available")
async def system_update_available() -> JSONResponse:
    """Return the cached git-update-check status for the UI banner.

    The check itself runs once on lifespan startup; this endpoint is a
    pure read of the cached dataclass. Never blocks, never triggers a
    new fetch. See `update_check.py` for the full contract.
    """
    from backend.App.integrations.infrastructure.update_check import status_as_dict
    return JSONResponse(status_as_dict())


@router.get("/health")
async def health() -> JSONResponse:
    checks: dict[str, Any] = {"status": "ok"}

    try:
        from backend.UI.REST.task_instance import task_store as _task_store
        client = getattr(_task_store, "client", None)
        if client:
            client.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "unavailable (in-memory fallback)"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        checks["status"] = "degraded"

    ollama_url = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(ollama_url.rstrip("/v1").rstrip("/") + "/api/tags")
            checks["ollama"] = "ok" if r.status_code == 200 else f"status {r.status_code}"
    except ImportError:
        checks["ollama"] = "unchecked"
    except Exception:
        checks["ollama"] = "unavailable"

    status_code = 200 if checks["status"] == "ok" else 207
    return JSONResponse(content=checks, status_code=status_code)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint (Histogram/Counter per pipeline step)."""
    resp = prometheus_metrics_response()
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail="Prometheus export off (SWARM_PROMETHEUS=0) or prometheus-client missing",
        )
    return resp


@router.get("/v1/observability/metrics")
async def observability_metrics() -> Any:
    return JSONResponse(content=step_metrics_snapshot())


# ---------------------------------------------------------------------------
# Defaults (frontend bootstrap)
# ---------------------------------------------------------------------------

@router.get("/v1/defaults")
async def get_defaults() -> JSONResponse:
    """Return frontend-consumable defaults and fallback policy metadata."""
    import json as _json
    from pathlib import Path as _Path
    from backend.App.integrations.infrastructure.llm.config import SWARM_MODEL_CLOUD_DEFAULT

    # --- Single source of truth: config/roles.json ---
    _roles_cfg_path = _Path(__file__).resolve().parents[4] / "config" / "roles.json"
    _roles_cfg = _json.loads(_roles_cfg_path.read_text(encoding="utf-8"))

    roles: list[str] = _roles_cfg["roles_order"]
    prompt_defaults: dict[str, str] = _roles_cfg["prompt_defaults"]
    prompt_choices: dict[str, list] = _roles_cfg["prompt_choices"]
    model_tier_overrides: dict[str, str] = _roles_cfg.get("model_tier_overrides", {})

    cloud_default = SWARM_MODEL_CLOUD_DEFAULT
    _swarm_model = os.getenv("SWARM_MODEL", "")
    ollama_planning = os.getenv("SWARM_MODEL_PLANNING") or _swarm_model
    ollama_generic = os.getenv("SWARM_MODEL_BUILD") or _swarm_model
    lmstudio_planning = os.getenv("SWARM_LMSTUDIO_MODEL_PLANNING") or ollama_planning
    lmstudio_generic = os.getenv("SWARM_LMSTUDIO_MODEL_BUILD") or ollama_generic

    model_defaults: dict[str, dict[str, str]] = {}
    for role in roles:
        tier = model_tier_overrides.get(role, "generic")
        role_upper = role.upper()
        ollama_specific = os.getenv(f"SWARM_MODEL_{role_upper}")
        lmstudio_specific = os.getenv(f"SWARM_LMSTUDIO_MODEL_{role_upper}")
        if tier == "planning":
            ollama_val = ollama_specific or os.getenv("SWARM_MODEL_PLANNING") or _swarm_model
            lmstudio_val = lmstudio_specific or lmstudio_planning
        else:
            ollama_val = ollama_specific or ollama_generic
            lmstudio_val = lmstudio_specific or lmstudio_generic
        model_defaults[role] = {
            "ollama": ollama_val,
            "lmstudio": lmstudio_val,
            "cloud": cloud_default,
        }
    remote_api_base_presets = {
        "anthropic": "",
        "openai_compatible": "https://api.openai.com/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "groq": "https://api.groq.com/openai/v1",
        "cerebras": "https://api.cerebras.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "ollama_cloud": "",
    }
    remote_profile_provider_options = [
        ["anthropic", "Anthropic (Claude)"],
        ["openai_compatible", "OpenAI / compatible URL"],
        ["gemini", "Google Gemini"],
        ["groq", "Groq"],
        ["cerebras", "Cerebras"],
        ["openrouter", "OpenRouter"],
        ["deepseek", "DeepSeek API"],
        ["ollama_cloud", "Ollama Cloud (custom URL)"],
    ]
    default_pipeline_order = [
        "clarify_input", "human_clarify_input", "pm", "review_pm", "human_pm",
        "ba", "review_ba", "human_ba", "architect", "review_stack", "review_arch",
        "human_arch", "ba_arch_debate", "spec_merge", "review_spec", "human_spec",
        "ux_researcher", "review_ux_researcher", "human_ux_researcher",
        "ux_architect", "review_ux_architect", "human_ux_architect",
        "ui_designer", "review_ui_designer", "human_ui_designer",
        "analyze_code", "generate_documentation", "problem_spotter", "refactor_plan",
        "human_code_review", "devops", "review_devops", "human_devops", "dev_lead",
        "review_dev_lead", "human_dev_lead", "dev", "review_dev", "human_dev", "qa",
        "review_qa", "human_qa",
        "seo_specialist", "review_seo_specialist", "human_seo_specialist",
        "ai_citation_strategist", "review_ai_citation_strategist", "human_ai_citation_strategist",
        "app_store_optimizer", "review_app_store_optimizer", "human_app_store_optimizer",
    ]

    return JSONResponse(content={
        "roles": roles,
        "model_defaults": model_defaults,
        "prompt_defaults": prompt_defaults,
        "prompt_choices": prompt_choices,
        "remote_api_base_presets": remote_api_base_presets,
        "remote_profile_provider_options": remote_profile_provider_options,
        "default_pipeline_order": default_pipeline_order,
        "default_role_environment": "ollama",
        "default_remote_api_provider": "anthropic",
        "default_swarm_provider": "ollama",
    })


# ---------------------------------------------------------------------------
# Pipeline plan
# ---------------------------------------------------------------------------

@router.post("/v1/pipeline/plan")
async def pipeline_plan(req: PipelinePlanRequest) -> Any:
    from backend.App.integrations.infrastructure.agent_registry import merge_agent_config
    from backend.App.integrations.infrastructure.swarm_planner import plan_pipeline_steps
    from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps

    agent_config = merge_agent_config(req.agent_config)

    def _run() -> dict[str, Any]:
        return plan_pipeline_steps(req.goal, agent_config=agent_config, constraints=req.constraints)

    plan_result = await asyncio.to_thread(_run)
    steps = plan_result.get("pipeline_steps")
    if isinstance(steps, list):
        try:
            validate_pipeline_steps([str(s).strip() for s in steps if str(s).strip()], agent_config)
            plan_result["validation_ok"] = True
        except ValueError as exc:
            plan_result["validation_ok"] = False
            plan_result["validation_error"] = str(exc)
    return JSONResponse(content=plan_result)


# ---------------------------------------------------------------------------
# MCP status / cache
# ---------------------------------------------------------------------------

@router.get("/v1/mcp/status")
def get_mcp_status(request: Request) -> JSONResponse:
    """Return MCP server autostart status."""
    manager = getattr(request.app.state, "mcp_manager", None)
    if not manager:
        return JSONResponse(content={"servers": {}, "autostart_enabled": False})
    return JSONResponse(content={"servers": manager.get_status(), "autostart_enabled": True})


@router.get("/v1/mcp/cache/stats")
async def get_mcp_cache_stats(request: Request) -> JSONResponse:
    """Return MCP tool result cache statistics (K-4)."""
    from backend.App.integrations.infrastructure.mcp.tool_cache import ToolResultCache

    cache: ToolResultCache = getattr(request.app.state, "mcp_tool_cache", None) or ToolResultCache()
    return JSONResponse(content=cache.stats())


# ---------------------------------------------------------------------------
# Workspace file listing (@ mention autocomplete)
# ---------------------------------------------------------------------------

@router.get("/v1/workspace/files")
async def get_workspace_files(workspace_root: str = Query("")) -> JSONResponse:
    """Return list of relative file paths in workspace for @ mention autocomplete."""
    import os as _os
    from pathlib import Path as _Path

    wr = workspace_root.strip()
    if not wr:
        return JSONResponse({"files": []})
    root = _Path(wr).expanduser().resolve()
    if not root.is_dir():
        return JSONResponse({"files": [], "error": "workspace_root not found"})

    _IGNORE_DIRS = frozenset({
        ".git", "__pycache__", "node_modules", ".venv", "venv", ".idea",
        ".vscode", "dist", "build", "target", ".mypy_cache", ".pytest_cache",
        "coverage", ".coverage", ".tox", ".eggs",
    })
    files: list[str] = []
    for dirpath, dirnames, filenames in _os.walk(str(root), topdown=True):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            rel = _Path(dirpath).relative_to(root) / name
            files.append(rel.as_posix())
            if len(files) >= 2000:
                break
        if len(files) >= 2000:
            break
    return JSONResponse({"files": files})
