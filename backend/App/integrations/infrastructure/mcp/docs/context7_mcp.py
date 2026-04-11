"""Context7 MCP server config — library documentation access.

Provides agents with read-only access to SDK/library documentation
via the ``@upstash/context7-mcp`` npm package.

Enabled explicitly via ``SWARM_CONTEXT7=1`` or
``agent_config.swarm.context7=true``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def context7_mcp_config() -> dict[str, Any]:
    """Return MCP server config dict for Context7 documentation server.

    Returns:
        MCP server config dict compatible with ``agent_config.mcp.servers``.
    """
    return {
        "name": "context7",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp@latest"],
    }
