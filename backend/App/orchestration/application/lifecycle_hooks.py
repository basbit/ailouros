"""Lifecycle hooks for the agent swarm pipeline.

Canonical location: backend/App/orchestration/application/lifecycle_hooks.py.
``orchestrator/lifecycle_hooks.py`` is kept as a re-export shim for backward compatibility.

Hooks (INV-1, INV-3):
  - session_preflight: runs before the first pipeline step; verifies npx/git/workspace_root.
    If MCP mode is requested but npx is unavailable -> explicit error, not silent fallback.
  - subagent_start: audit log + SSE event emitted at the start of each pipeline step.
  - pre_tool_use: validates MCP tool calls against the task's allowed_tools policy.

None of these hooks silently degrade -- they raise or log explicitly.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Optional

logger = logging.getLogger(__name__)


def build_preflight_recommendations(
    workspace_root: str,
    context_mode: str,
    *,
    mcp_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return transparent capability/server recommendations for preflight UI."""
    servers = list((mcp_config or {}).get("servers") or []) if isinstance(mcp_config, dict) else []
    server_names = {
        str(item.get("name") or "").strip()
        for item in servers
        if isinstance(item, dict) and item.get("enabled", True)
    }
    brave_key = (os.getenv("SWARM_BRAVE_SEARCH_API_KEY") or "").strip()
    if not brave_key and isinstance((mcp_config or {}).get("swarm"), dict):
        brave_key = str(((mcp_config or {}).get("swarm") or {}).get("brave_search_api_key") or "").strip()
    ddg_available = False
    try:
        from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import ddg_search_available

        ddg_available = bool(ddg_search_available())
    except Exception:
        ddg_available = False

    recommended_capabilities = [
        {
            "name": "repo_evidence_tools",
            "recommended": True,
            "available": "filesystem" in server_names or context_mode in ("retrieve_fs", "retrieve", "full"),
            "reason": "Repository-backed evidence and file inspection keep planning grounded in workspace facts.",
        },
        {
            "name": "git_history",
            "recommended": True,
            "available": "git" in server_names or shutil.which("git") is not None,
            "reason": "Git diff/log access improves review, regression analysis and patch explainability.",
        },
        {
            "name": "internet_search",
            "recommended": True,
            "available": ("brave_search" in server_names) or bool(brave_key) or ddg_available,
            "reason": "Needed for external research, current facts and website/vendor verification.",
        },
    ]

    recommended_servers = [
        {
            "name": "filesystem",
            "recommended": True,
            "enabled": "filesystem" in server_names,
            "reason": "Primary workspace file access for repo evidence and targeted reads.",
        },
        {
            "name": "git",
            "recommended": True,
            "enabled": "git" in server_names,
            "reason": "History/diff context without inflating prompts.",
        },
        {
            "name": "brave_search",
            "recommended": True,
            "enabled": "brave_search" in server_names,
            "reason": "Recommended when tasks require internet/web search or current fact verification.",
        },
    ]
    return {
        "recommended_capabilities": recommended_capabilities,
        "recommended_servers": recommended_servers,
    }


# ---------------------------------------------------------------------------
# SessionStart preflight (G-2 §1)
# ---------------------------------------------------------------------------

class PreflightError(RuntimeError):
    """Raised when a critical preflight check fails (used to halt pipeline start)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_session_preflight(
    workspace_root: str,
    context_mode: str,
    *,
    require_git: bool = False,
) -> dict[str, Any]:
    """Run preflight checks before starting a pipeline session.

    Returns a dict with results suitable for inclusion in an SSE ``session_preflight`` event.

    Raises:
        PreflightError: if a blocking check fails (e.g. MCP mode + npx unavailable).
    """
    npx_ok = shutil.which("npx") is not None
    git_ok = shutil.which("git") is not None
    workspace_ok = bool(workspace_root and os.path.isdir(workspace_root))

    result: dict[str, Any] = {
        "type": "session_preflight",
        "npx_available": npx_ok,
        "git_available": git_ok,
        "workspace_root_exists": workspace_ok,
        "workspace_root": workspace_root or "",
        "context_mode": context_mode,
        "status": "ok",
    }
    result.update(build_preflight_recommendations(workspace_root, context_mode))

    # MCP mode requires npx (INV-3)
    if context_mode in ("retrieve_mcp", "retrieve+mcp") and not npx_ok:
        result["status"] = "failed"
        result["error_code"] = "MCP_UNAVAILABLE"
        result["error"] = (
            f"context_mode={context_mode} requires npx, but npx was not found in PATH. "
            "Install Node.js or switch to retrieve_fs mode."
        )
        logger.error(
            "session_preflight FAILED: code=MCP_UNAVAILABLE context_mode=%s npx=%s",
            context_mode,
            npx_ok,
        )
        raise PreflightError(
            "MCP_UNAVAILABLE",
            result["error"],
        )

    if require_git and not git_ok:
        result["status"] = "degraded"
        result["warning"] = "git not found in PATH -- diff operations will be unavailable"
        logger.warning("session_preflight: git not found; diff operations will fail")

    if workspace_root and not workspace_ok:
        result["status"] = "degraded"
        result["warning"] = f"workspace_root does not exist: {workspace_root}"
        logger.warning("session_preflight: workspace_root does not exist: %s", workspace_root)

    logger.info(
        "session_preflight: status=%s context_mode=%s npx=%s git=%s workspace=%s",
        result["status"],
        context_mode,
        npx_ok,
        git_ok,
        workspace_ok,
    )
    return result


# ---------------------------------------------------------------------------
# SubagentStart audit (G-2 §2)
# ---------------------------------------------------------------------------

def build_subagent_start_event(
    step_id: str,
    agent: str,
    context_mode: str,
    tools_enabled: bool,
) -> dict[str, Any]:
    """Build a ``subagent_start`` SSE event dict for the given step.

    The caller is responsible for yielding this event in the SSE stream.
    """
    event: dict[str, Any] = {
        "type": "subagent_start",
        "step_id": step_id,
        "agent": agent,
        "context_mode": context_mode,
        "tools_enabled": tools_enabled,
    }
    logger.info(
        "subagent_start: step=%s agent=%s context_mode=%s tools_enabled=%s",
        step_id,
        agent,
        context_mode,
        tools_enabled,
    )
    return event


# ---------------------------------------------------------------------------
# PreToolUse validator (G-2 §3)
# ---------------------------------------------------------------------------

class ToolNotAllowedError(PermissionError):
    """Raised when a tool call is rejected by the allowed_tools policy."""

    def __init__(self, tool_name: str, allowed: list[str]) -> None:
        super().__init__(
            f"Tool '{tool_name}' is not in the allowed_tools list: {allowed}. "
            "Add it to task_spec.tools_policy.allowed_tools or use a broader policy."
        )
        self.tool_name = tool_name
        self.allowed = allowed


def validate_tool_use(
    tool_name: str,
    tools_policy: Optional[dict[str, Any]],
) -> None:
    """Validate *tool_name* against *tools_policy*.

    ``tools_policy`` mirrors ``task_spec.tools_policy`` from the artifact schema:
    ``{ "tools_enabled": true, "allowed_tools": ["list"] }``

    If ``tools_enabled`` is False -> all tools rejected.
    If ``allowed_tools`` is a non-empty list -> tool must be in it.
    An empty/absent ``allowed_tools`` means "all tools allowed" (when ``tools_enabled=True``).

    Raises:
        ToolNotAllowedError: if the tool is not permitted (INV-1: explicit error, no silent pass).
    """
    if not tools_policy:
        return  # No policy -> all allowed

    tools_enabled = tools_policy.get("tools_enabled", True)
    if not tools_enabled:
        raise ToolNotAllowedError(tool_name, [])

    allowed: list[str] = tools_policy.get("allowed_tools") or []
    if allowed and tool_name not in allowed:
        logger.warning(
            "pre_tool_use REJECTED: tool=%s not in allowed_tools=%s",
            tool_name,
            allowed,
        )
        raise ToolNotAllowedError(tool_name, allowed)

    logger.debug("pre_tool_use allowed: tool=%s", tool_name)
