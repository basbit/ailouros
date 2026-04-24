from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ddg_search_available() -> bool:
    try:
        import duckduckgo_search as _ddg_check
        del _ddg_check
        return True
    except ImportError:
        return False


def ddg_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as exc:
        logger.warning("DuckDuckGo search failed: %s", exc)
        return []


def ddg_search_mcp_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Use for finding current information, documentation, examples.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                },
                "required": ["query"],
            },
        },
    }
