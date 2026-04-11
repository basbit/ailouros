"""Git MCP server auto-injection config.

Injects ``mcp-server-git`` (Python package) via ``uvx`` when the workspace
contains a ``.git`` folder.

Requires ``uv`` to be installed:
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # or: brew install uv

Read-only by default.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def git_mcp_config(workspace_root: str) -> dict[str, Any]:
    """Return MCP server config dict for git operations.

    Uses ``mcp-server-git`` via ``uvx`` (Python package, NOT npm).

    Args:
        workspace_root: Absolute path to the git repository root.

    Returns:
        MCP server config dict compatible with ``agent_config.mcp.servers``.
    """
    return {
        "name": "git",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository", workspace_root],
    }


def git_mcp_available() -> bool:
    """Check if uvx is available on PATH."""
    import shutil
    return shutil.which("uvx") is not None


def workspace_has_git(workspace_root: str) -> bool:
    """Check if workspace_root contains a .git directory."""
    wr = (workspace_root or "").strip()
    if not wr:
        return False
    return (Path(wr) / ".git").exists()
