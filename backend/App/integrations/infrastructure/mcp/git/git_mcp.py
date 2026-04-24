from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def git_mcp_config(workspace_root: str) -> dict[str, Any]:
    return {
        "name": "git",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository", workspace_root],
    }


def git_mcp_available() -> bool:
    import shutil
    return shutil.which("uvx") is not None


def workspace_has_git(workspace_root: str) -> bool:
    root_stripped = (workspace_root or "").strip()
    if not root_stripped:
        return False
    return (Path(root_stripped) / ".git").exists()
