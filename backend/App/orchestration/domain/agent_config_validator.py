from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TOOLS_ONLY = "tools_only"
_INDEX_ONLY = "index_only"
_RETRIEVE = "retrieve"


def validate_tools_only_mcp_state(
    context_mode: str,
    workspace_root: str,
    mcp_servers: list[Any],
) -> None:
    if context_mode != _TOOLS_ONLY:
        return
    if not workspace_root.strip():
        return
    if mcp_servers:
        return
    raise ValueError(
        "workspace_context_mode=tools_only requires MCP servers (enable SWARM_MCP_AUTO and npx in PATH, "
        "or set agent_config.mcp.servers)"
    )


def warn_workspace_context_vs_custom_pipeline(
    context_mode: str,
    step_ids: list[str],
    task_id_prefix: str = "",
) -> list[str]:
    warnings: list[str] = []
    if context_mode not in (_INDEX_ONLY, _RETRIEVE, _TOOLS_ONLY):
        return warnings
    if not step_ids:
        return warnings
    if "analyze_code" in step_ids:
        return warnings
    msg = (
        f"workspace_context_mode={context_mode} but pipeline_steps has no analyze_code — "
        f"structured code context may be missing (task_id={task_id_prefix})"
    )
    warnings.append(msg)
    return warnings
