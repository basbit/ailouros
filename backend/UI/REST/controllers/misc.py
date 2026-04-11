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
    from backend.App.integrations.infrastructure.llm.config import SWARM_MODEL_CLOUD_DEFAULT

    roles = [
        "pm", "ba", "architect", "reviewer", "stack_reviewer",
        "dev", "qa", "problem_spotter", "refactor_plan",
        "code_diagram", "doc_generate", "devops", "dev_lead",
    ]

    cloud_default = SWARM_MODEL_CLOUD_DEFAULT
    _swarm_model = os.getenv("SWARM_MODEL", "")
    ollama_default_pm = os.getenv("SWARM_MODEL_PM") or os.getenv("SWARM_MODEL_PLANNING") or _swarm_model
    ollama_default_dev_lead = os.getenv("SWARM_MODEL_DEV_LEAD") or os.getenv("SWARM_MODEL_PLANNING") or _swarm_model
    ollama_default_generic = os.getenv("SWARM_MODEL_BUILD") or _swarm_model
    lmstudio_default_pm = os.getenv("SWARM_LMSTUDIO_MODEL_PM") or ollama_default_pm
    lmstudio_default_dev_lead = os.getenv("SWARM_LMSTUDIO_MODEL_DEV_LEAD") or ollama_default_dev_lead
    lmstudio_default_generic = os.getenv("SWARM_LMSTUDIO_MODEL_BUILD") or ollama_default_generic

    model_defaults: dict[str, dict[str, str]] = {}
    for role in roles:
        if role in ("pm", "dev_lead"):
            model_defaults[role] = {
                "ollama": ollama_default_pm if role == "pm" else ollama_default_dev_lead,
                "lmstudio": lmstudio_default_pm if role == "pm" else lmstudio_default_dev_lead,
                "cloud": cloud_default,
            }
        else:
            model_defaults[role] = {
                "ollama": ollama_default_generic,
                "lmstudio": lmstudio_default_generic,
                "cloud": cloud_default,
            }

    prompt_defaults = {
        "pm": "project-management/project-manager-senior.md",
        "ba": "product/product-requirements-analyst.md",
        "architect": "engineering/engineering-software-architect.md",
        "reviewer": "specialized/specialized-reviewer.md",
        "stack_reviewer": "specialized/specialized-reviewer.md",
        "dev": "engineering/engineering-senior-developer.md",
        "qa": "specialized/software-qa-engineer.md",
        "problem_spotter": "specialized/code-problem-spotter.md",
        "refactor_plan": "specialized/code-refactor-planner.md",
        "code_diagram": "specialized/code-structure-diagram.md",
        "doc_generate": "specialized/code-doc-generator.md",
        "devops": "engineering/devops-setup.md",
        "dev_lead": "project-management/dev-lead.md",
    }
    prompt_choices = {
        "pm": [
            ["project-management/project-manager-senior.md", "PM senior"],
            ["project-management/project-management-project-shepherd.md", "Shepherd"],
            ["__custom__", "Custom…"],
        ],
        "ba": [
            ["product/product-requirements-analyst.md", "BA requirements"],
            ["product/product-manager.md", "Product manager"],
            ["__custom__", "Custom…"],
        ],
        "architect": [["engineering/engineering-software-architect.md", "Architect"], ["__custom__", "Custom…"]],
        "reviewer": [["specialized/specialized-reviewer.md", "Reviewer prompt"], ["__custom__", "Custom…"]],
        "stack_reviewer": [["specialized/specialized-reviewer.md", "Reviewer prompt"], ["__custom__", "Custom…"]],
        "dev": [["engineering/engineering-senior-developer.md", "Senior dev"], ["__custom__", "Custom…"]],
        "qa": [
            ["specialized/software-qa-engineer.md", "Software QA"],
            ["specialized/specialized-model-qa.md", "Model QA (ML)"],
            ["specialized/specialized-reviewer.md", "Reviewer prompt"],
            ["__custom__", "Custom…"],
        ],
        "problem_spotter": [["specialized/code-problem-spotter.md", "Problem spotter"], ["__custom__", "Custom…"]],
        "refactor_plan": [["specialized/code-refactor-planner.md", "Refactor planner"], ["__custom__", "Custom…"]],
        "code_diagram": [["specialized/code-structure-diagram.md", "Structure diagram"], ["__custom__", "Custom…"]],
        "doc_generate": [
            ["specialized/code-doc-generator.md", "Code doc generator"],
            ["specialized/specialized-document-generator.md", "Doc generator (alt)"],
            ["__custom__", "Custom…"],
        ],
        "devops": [
            ["engineering/devops-setup.md", "DevOps setup"],
            ["engineering/engineering-devops-automator.md", "DevOps automator"],
            ["__custom__", "Custom…"],
        ],
        "dev_lead": [["project-management/dev-lead.md", "Dev lead"], ["__custom__", "Custom…"]],
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
        "analyze_code", "generate_documentation", "problem_spotter", "refactor_plan",
        "human_code_review", "devops", "review_devops", "human_devops", "dev_lead",
        "review_dev_lead", "human_dev_lead", "dev", "review_dev", "human_dev", "qa",
        "review_qa", "human_qa",
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
# Circuit breakers / failure types
# ---------------------------------------------------------------------------

@router.get("/v1/circuit-breakers")
async def get_circuit_breakers() -> JSONResponse:
    """Return current circuit breaker states for all tracked MCP tools."""
    from backend.App.integrations.infrastructure.mcp.circuit_breaker import get_registry
    states = get_registry().get_all_states()
    return JSONResponse(content={"circuit_breakers": states})


@router.get("/v1/failure-types")
async def get_failure_types() -> JSONResponse:
    """Return all FailureType values with descriptions and retry hints."""
    from backend.App.orchestration.domain.failure_types import FailureType

    _descriptions: dict[str, dict[str, str]] = {
        FailureType.TIMEOUT.value: {
            "description": "Operation timed out",
            "mitigation": "Retry with increased timeout (SWARM_RETRY_TIMEOUT_MULTIPLIER=1.5)",
        },
        FailureType.CONTEXT_OVERFLOW.value: {
            "description": "Context window or token limit exceeded",
            "mitigation": "Retry with reduced context (drop low-priority sections or use index_only mode)",
        },
        FailureType.LOGIC_ERROR.value: {
            "description": "Python exception or structured output parse failure",
            "mitigation": "Retry with error feedback prepended to prompt",
        },
        FailureType.EXTERNAL_API.value: {
            "description": "HTTP / external API error (5xx, rate limit, connection drop)",
            "mitigation": "Exponential backoff then retry (1s, 2s, 4s); circuit breaker applies",
        },
        FailureType.MODEL_REFUSAL.value: {
            "description": "Model returned a refusal or empty completion",
            "mitigation": "Switch to alternative fallback model",
        },
        FailureType.MCP_FAILURE.value: {
            "description": "MCP tool-layer error (stdio, tools/call)",
            "mitigation": "Retry without MCP tools (tools_off=True)",
        },
        FailureType.UNKNOWN.value: {
            "description": "Unrecognised failure pattern",
            "mitigation": "Manual investigation required",
        },
    }
    return JSONResponse(content={"failure_types": _descriptions})


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
