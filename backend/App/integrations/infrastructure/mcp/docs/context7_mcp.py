from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def context7_mcp_config() -> dict[str, Any]:
    return {
        "name": "context7",
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp@latest"],
    }
