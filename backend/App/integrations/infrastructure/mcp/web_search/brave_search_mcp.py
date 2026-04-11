"""Brave Search MCP integration for web search in pipeline agents."""
from __future__ import annotations

from typing import Any


def brave_search_mcp_config(api_key: str) -> dict[str, Any]:
    """Return MCP server config dict for Brave Search.

    The config uses ``@modelcontextprotocol/server-brave-search`` via npx.
    Pass the result as a server entry inside ``agent_config.mcp.servers``.

    Args:
        api_key: Brave Search API key (``BRAVE_API_KEY``).

    Returns:
        MCP server config dict compatible with ``agent_config.mcp.servers``.
    """
    return {
        "name": "brave_search",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env": {"BRAVE_API_KEY": api_key},
    }
