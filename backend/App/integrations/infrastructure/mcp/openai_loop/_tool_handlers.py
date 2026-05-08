from __future__ import annotations

import os
from typing import Any


def handle_web_search(args: dict[str, Any]) -> str:
    from backend.App.integrations.infrastructure.mcp.web_search.web_search_router import (
        web_search,
    )
    query = args.get("query", "")
    if not query:
        return "ERROR: 'query' parameter is required for web_search"
    try:
        results = web_search(query, max_results=5)
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        return f"ERROR: web search failed — {exc}"
    if not results:
        return f"No results found for: {query}"
    return _format_search_results(results)


def handle_ddg_search(args: dict[str, Any]) -> str:
    from backend.App.integrations.infrastructure.mcp.web_search.ddg_search import (
        ddg_search,
        ddg_search_available,
    )
    if not ddg_search_available():
        return (
            "ERROR: DuckDuckGo search unavailable — "
            "package 'duckduckgo-search' is not installed. "
            "Set SWARM_TAVILY_API_KEY, SWARM_EXA_API_KEY, or "
            "SWARM_SCRAPINGDOG_API_KEY to use the multi-provider web search "
            "router instead."
        )
    query = args.get("query", "")
    if not query:
        return "ERROR: 'query' parameter is required for web_search"
    results = ddg_search(query, max_results=5)
    if not results:
        return f"No results found for: {query}"
    return _format_search_results(results)


def handle_fetch_page(args: dict[str, Any]) -> str:
    from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
        fetch_page,
    )
    url = args.get("url", "")
    return fetch_page(url)


def handle_local_evidence_tool(name: str, args: dict[str, Any]) -> str:
    from backend.App.integrations.infrastructure.mcp.evidence_tools import (
        find_class_definition,
        find_symbol_usages,
        grep_context,
    )
    workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()
    if not workspace_root:
        return (
            "ERROR: SWARM_WORKSPACE_ROOT is not set; "
            "local evidence tools are unavailable."
        )
    if name == "grep_context":
        return grep_context(
            workspace_root,
            query=str(args.get("query") or ""),
            globs=args.get("globs") if isinstance(args.get("globs"), list) else None,
            max_hits=int(args.get("max_hits") or 5),
        )
    if name == "find_class_definition":
        return find_class_definition(
            workspace_root,
            symbol=str(args.get("symbol") or ""),
        )
    if name == "find_symbol_usages":
        return find_symbol_usages(
            workspace_root,
            symbol=str(args.get("symbol") or ""),
            max_hits=int(args.get("max_hits") or 10),
        )
    return f"ERROR: unsupported local evidence tool: {name}"


def handle_wiki_tool(name: str, args: dict[str, Any]) -> str:
    from backend.App.integrations.infrastructure.mcp.wiki_tools import (
        handle_wiki_tool_call,
    )
    workspace_root = os.getenv("SWARM_WORKSPACE_ROOT", "").strip()
    if not workspace_root:
        return (
            "ERROR: SWARM_WORKSPACE_ROOT is not set; wiki tools are unavailable."
        )
    return handle_wiki_tool_call(name, args, workspace_root)


def _format_search_results(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for result in results:
        lines.append(f"**{result.get('title', '')}**")
        lines.append(f"URL: {result.get('href', '')}")
        lines.append(result.get("body", ""))
        lines.append("")
    return "\n".join(lines)


__all__ = (
    "handle_web_search",
    "handle_ddg_search",
    "handle_fetch_page",
    "handle_local_evidence_tool",
    "handle_wiki_tool",
)
