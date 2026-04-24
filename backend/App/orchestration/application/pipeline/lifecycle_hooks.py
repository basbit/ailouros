
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
    search_api_keys: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    servers = list((mcp_config or {}).get("servers") or []) if isinstance(mcp_config, dict) else []
    server_names = {
        str(item.get("name") or "").strip()
        for item in servers
        if isinstance(item, dict) and item.get("enabled", True)
    }
    _keys = search_api_keys or {}
    search_key_available = bool(
        _keys.get("tavily") or _keys.get("exa") or _keys.get("scrapingdog")
        or os.getenv("SWARM_TAVILY_API_KEY", "")
        or os.getenv("SWARM_EXA_API_KEY", "")
        or os.getenv("SWARM_SCRAPINGDOG_API_KEY", "")
    )
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
            "available": ("web_search" in server_names) or search_key_available or ddg_available,
            "reason": "Needed for external research, current facts and website/vendor verification.",
        },
    ]

    _known_server_names = {"filesystem", "git", "web_search"}
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
            "name": "web_search",
            "recommended": True,
            "enabled": "web_search" in server_names or search_key_available,
            "reason": "Recommended when tasks require internet/web search or current fact verification.",
        },
    ]
    for item in servers:
        if not isinstance(item, dict):
            continue
        sname = str(item.get("name") or "").strip()
        if sname and sname not in _known_server_names:
            recommended_servers.append({
                "name": sname,
                "recommended": False,
                "enabled": bool(item.get("enabled", True)),
                "reason": item.get("reason", "Configured via mcp_config."),
            })
    return {
        "recommended_capabilities": recommended_capabilities,
        "recommended_servers": recommended_servers,
    }


class PreflightError(RuntimeError):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_session_preflight(
    workspace_root: str,
    context_mode: str,
    *,
    require_git: bool = False,
) -> dict[str, Any]:
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


def build_subagent_start_event(
    step_id: str,
    agent: str,
    context_mode: str,
    tools_enabled: bool,
) -> dict[str, Any]:
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


class ToolNotAllowedError(PermissionError):

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
