"""GitHub MCP server config — issues, PRs, code access.

Injects ``@modelcontextprotocol/server-github`` as an MCP server when
``GITHUB_TOKEN`` (or ``GITHUB_PERSONAL_ACCESS_TOKEN``) is set.

Read-only by default. The token's permissions determine what the
agent can access.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def github_token() -> str:
    """Return the GitHub token from environment, or empty string."""
    return (
        (os.getenv("GITHUB_TOKEN") or "").strip()
        or (os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or "").strip()
    )


def github_mcp_config(token: str) -> dict[str, Any]:
    """Return MCP server config dict for GitHub.

    Args:
        token: GitHub personal access token or app token.

    Returns:
        MCP server config dict compatible with ``agent_config.mcp.servers``.
    """
    return {
        "name": "github",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
