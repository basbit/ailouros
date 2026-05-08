from __future__ import annotations

from typing import Any


def build_runtime_telemetry(
    agent_config: dict[str, Any] | None,
    workspace_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    config = agent_config if isinstance(agent_config, dict) else {}
    meta = workspace_meta if isinstance(workspace_meta, dict) else {}
    mcp_config_raw = config.get("mcp")
    mcp_config: dict[str, Any] = mcp_config_raw if isinstance(mcp_config_raw, dict) else {}
    servers_raw = mcp_config.get("servers")
    servers = servers_raw if isinstance(servers_raw, list) else []
    context_mode = str(meta.get("workspace_context_mode") or "").strip()
    has_fallback = bool(meta.get("workspace_context_mcp_fallback"))
    has_tools = bool(servers)
    if has_fallback:
        phase = "fallback"
    elif has_tools:
        phase = "ready"
    else:
        phase = "off"
    telemetry: dict[str, Any] = {
        "context_mode": context_mode,
        "tools_enabled": has_tools,
        "mcp_phase": phase,
    }
    return {key: value for key, value in telemetry.items() if value != ""}
