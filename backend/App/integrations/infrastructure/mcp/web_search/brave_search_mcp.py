"""Brave Search has been removed. Use web_search_router instead.

This module is kept as a shim so that any remaining imports do not crash.
All web search functionality is now handled by web_search_router.py which
supports Tavily, Exa, and ScrapingDog with automatic monthly rotation.
"""
from __future__ import annotations

# Re-export from the new router so existing import sites don't break
from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import (  # noqa: F401
    web_search,
    web_search_available,
    web_search_mcp_tool_definition,
)
