from __future__ import annotations

from typing import Any


def check_mcp_server_preflight(server_config: dict[str, Any]) -> dict[str, Any]:
    from backend.App.integrations.infrastructure.mcp.stdio.session import mcp_preflight_check
    return mcp_preflight_check(server_config)
