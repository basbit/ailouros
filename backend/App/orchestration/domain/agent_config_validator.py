"""Domain: pure validation logic for agent configuration.

No infrastructure imports — only stdlib and typing (INV-7).
These functions operate on plain values extracted from the pipeline state dict
rather than on the state dict itself, so they remain testable in isolation.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Context mode constants — mirrored here to avoid importing workspace infrastructure.
# Must stay in sync with WORKSPACE_CONTEXT_MODE_* in workspace_io.
_TOOLS_ONLY = "tools_only"
_INDEX_ONLY = "index_only"
_RETRIEVE = "retrieve"


def validate_tools_only_mcp_state(
    context_mode: str,
    workspace_root: str,
    mcp_servers: list[Any],
) -> None:
    """Raise ValueError if tools-only mode is misconfigured.

    The function is a no-op when the context mode is not ``tools_only`` or
    when no workspace root is set.  It raises only when the combination of
    inputs would result in a silent, hard-to-diagnose failure at runtime.

    Args:
        context_mode: Normalised workspace context mode string.
        workspace_root: Workspace root path (empty string if not set).
        mcp_servers: List of configured MCP server definitions.

    Raises:
        ValueError: When ``tools_only`` mode is active, a workspace root is
            set, but no MCP servers are configured.
    """
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
    """Return a list of warning messages for incompatible context mode + pipeline steps.

    Returns an empty list when no warnings apply.  The caller decides whether
    to log, emit SSE events, or discard the messages.

    Args:
        context_mode: Normalised workspace context mode string.
        step_ids: Pipeline step identifiers from the request.
        task_id_prefix: Optional task identifier prefix for log correlation.

    Returns:
        List of human-readable warning strings (may be empty).
    """
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
