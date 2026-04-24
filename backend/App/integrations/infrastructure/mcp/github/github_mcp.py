from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def github_token() -> str:
    return (
        (os.getenv("GITHUB_TOKEN") or "").strip()
        or (os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or "").strip()
    )


def github_mcp_config(token: str) -> dict[str, Any]:
    return {
        "name": "github",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }
