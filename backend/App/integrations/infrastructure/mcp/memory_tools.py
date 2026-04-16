"""MCP tool definitions for swarm memory backends.

Exposes four tools that agents can call via the MCP tool-call protocol:

1. ``search_memory``    — fan-out search across all memory backends.
2. ``store_pattern``    — persist a key/value pattern.
3. ``store_episode``    — append an episode to cross-task memory.
4. ``get_past_failures``— retrieve past failure records filtered by query.

Toggle: ``SWARM_MEMORY_TOOLS=1`` (default OFF — no behaviour change unless set).

Pattern follows ``evidence_tools.py``:
  - ``memory_tools_available(workspace_root)``
  - ``memory_tools_definitions()``
  - ``handle_memory_tool_call(tool_name, arguments, state)``
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "memory_tools_available",
    "memory_tools_definitions",
    "handle_memory_tool_call",
]


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def _memory_tools_enabled() -> bool:
    return os.getenv("SWARM_MEMORY_TOOLS", "0").strip() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def memory_tools_available(workspace_root: str) -> bool:
    """Return True when memory tools are enabled and workspace_root is a valid directory.

    Args:
        workspace_root: Absolute path to the target workspace.

    Returns:
        True only when SWARM_MEMORY_TOOLS=1 and workspace_root is a real directory.
    """
    root = (workspace_root or "").strip()
    if not root:
        return False
    if not _memory_tools_enabled():
        return False
    return Path(root).expanduser().is_dir()


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------


def memory_tools_definitions() -> list[dict[str, Any]]:
    """Return the list of MCP tool definitions for memory operations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": (
                    "Fan-out search across all memory backends (patterns, episodes, wiki). "
                    "Returns the most relevant stored knowledge for the given query."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text search query",
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Memory namespace (default: 'default')",
                            "default": "default",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return per backend",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "store_pattern",
                "description": (
                    "Store a key/value pattern in pattern memory for future retrieval. "
                    "Existing values for the same key are merged (appended)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Unique pattern key (e.g. 'auth:jwt-refresh')",
                        },
                        "value": {
                            "type": "string",
                            "description": "Pattern text to store",
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Pattern namespace (default: 'default')",
                            "default": "default",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "store_episode",
                "description": (
                    "Append an episode to cross-task memory. "
                    "Episodes are used to build context across pipeline runs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "body": {
                            "type": "string",
                            "description": "Episode content (free text or structured JSON)",
                        },
                        "step_id": {
                            "type": "string",
                            "description": "Pipeline step that produced this episode",
                            "default": "mcp",
                        },
                        "namespace": {
                            "type": "string",
                            "description": "Memory namespace (default: 'default')",
                            "default": "default",
                        },
                    },
                    "required": ["body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_past_failures",
                "description": (
                    "Retrieve past pipeline failure records relevant to the given query. "
                    "Useful for avoiding known failure modes before starting a step."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Query text to match against failure summaries and contexts",
                            "default": "",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of failure records to return",
                            "default": 5,
                        },
                    },
                    "required": [],
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_search_memory(arguments: dict[str, Any], state: dict[str, Any]) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)
    limit = int(arguments.get("limit") or 5)
    limit = max(1, min(20, limit))

    from backend.App.integrations.infrastructure.unified_memory import search_memory

    hits = search_memory(state, query, limit=limit)
    results = [
        {
            "source": h.source,
            "label": h.label,
            "body": h.body[:800],
            "score": round(h.score, 4),
        }
        for h in hits
    ]
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False, indent=2)


def _handle_store_pattern(arguments: dict[str, Any], state: dict[str, Any]) -> str:
    key = str(arguments.get("key") or "").strip()
    value = str(arguments.get("value") or "").strip()
    namespace = str(arguments.get("namespace") or "default").strip() or "default"

    if not key or not value:
        return json.dumps({"error": "key and value are required"}, ensure_ascii=False)

    from backend.App.integrations.infrastructure.pattern_memory import (
        pattern_memory_path_for_state,
        store_pattern,
    )

    path = pattern_memory_path_for_state(state)
    try:
        store_pattern(path, namespace, key, value)
    except Exception as exc:
        logger.warning("memory_tools: store_pattern failed: %s", exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    logger.info("memory_tools: stored pattern key=%r namespace=%r", key, namespace)
    return json.dumps({"stored": True, "key": key, "namespace": namespace}, ensure_ascii=False)


def _handle_store_episode(arguments: dict[str, Any], state: dict[str, Any]) -> str:
    body = str(arguments.get("body") or "").strip()
    step_id = str(arguments.get("step_id") or "mcp").strip() or "mcp"
    namespace = str(arguments.get("namespace") or "default").strip() or "default"

    if not body:
        return json.dumps({"error": "body is required"}, ensure_ascii=False)

    from backend.App.integrations.infrastructure.cross_task_memory import append_episode

    # Build a minimal state that targets the requested namespace
    episode_state: dict[str, Any] = {
        **state,
        "agent_config": {
            **(state.get("agent_config") or {}),
            "swarm": {
                **((state.get("agent_config") or {}).get("swarm") or {}),
                "cross_task_memory": {
                    "enabled": True,
                    "namespace": namespace,
                },
            },
        },
    }
    try:
        append_episode(episode_state, step_id=step_id, body=body)
    except Exception as exc:
        logger.warning("memory_tools: append_episode failed: %s", exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    logger.info("memory_tools: appended episode step=%r namespace=%r", step_id, namespace)
    return json.dumps({"stored": True, "step_id": step_id, "namespace": namespace}, ensure_ascii=False)


def _handle_get_past_failures(arguments: dict[str, Any], state: dict[str, Any]) -> str:
    query = str(arguments.get("query") or "").strip()
    limit = int(arguments.get("limit") or 5)
    limit = max(1, min(20, limit))

    from backend.App.integrations.infrastructure.failure_memory import get_warnings_for

    # When query is empty, use a broad placeholder so token overlap still fires
    search_text = query if query else " "
    try:
        warnings = get_warnings_for(state, search_text, limit=limit, min_score=0.0)
    except Exception as exc:
        logger.warning("memory_tools: get_warnings_for failed: %s", exc)
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    # Filter by query when one is provided (token overlap already does this,
    # but make the result consistent with the documented behaviour).
    if query:
        q_lower = query.lower()
        warnings = [
            w for w in warnings
            if q_lower in str(w.get("summary") or "").lower()
            or q_lower in str(w.get("context") or "").lower()
            or w.get("score", 0) > 0
        ]

    results = [
        {
            "step": w.get("step"),
            "summary": w.get("summary"),
            "count": w.get("count"),
            "last_seen": w.get("last_seen"),
            "score": round(float(w.get("score", 0.0)), 4),
        }
        for w in warnings[:limit]
    ]
    return json.dumps({"failures": results, "count": len(results)}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS = {
    "search_memory": _handle_search_memory,
    "store_pattern": _handle_store_pattern,
    "store_episode": _handle_store_episode,
    "get_past_failures": _handle_get_past_failures,
}


def handle_memory_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Dispatch a memory tool call and return a JSON string result.

    Args:
        tool_name:  One of the names returned by :func:`memory_tools_definitions`.
        arguments:  Parsed argument dict from the LLM tool call.
        state:      Pipeline state dict (used to resolve paths, config, etc.).

    Returns:
        JSON string with the tool result.

    Raises:
        ValueError: When *tool_name* is not a known memory tool.
    """
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(
            f"Unknown memory tool: {tool_name!r}. "
            f"Available: {sorted(_HANDLERS)}"
        )
    return handler(arguments, state)
