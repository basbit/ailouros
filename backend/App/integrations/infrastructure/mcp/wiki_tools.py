from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WIKI_REL_ROOT = Path(".swarm") / "wiki"
_MAX_READ_CHARS = 8000


def _wiki_root(workspace_root: str) -> Path:
    return Path(workspace_root).expanduser() / _WIKI_REL_ROOT


def wiki_tools_available(workspace_root: str) -> bool:
    if os.environ.get("SWARM_WIKI_TOOLS", "0").strip() != "1":
        return False
    root = (workspace_root or "").strip()
    return bool(root) and _wiki_root(root).is_dir()


def wiki_tools_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "wiki_search",
                "description": (
                    "Search the project wiki for paragraphs relevant to a query. "
                    "Returns a JSON list of {rel_path, section, text, score} objects. "
                    "Use this to look up prior decisions, architecture notes, or feature docs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language search query",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default 5)",
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
                "name": "wiki_read",
                "description": (
                    "Read the full content of a wiki article by its relative path "
                    "(without the .md extension). Returns up to 8000 characters."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rel_path": {
                            "type": "string",
                            "description": (
                                "Relative path inside the wiki root, without .md extension. "
                                "Example: 'architecture/pipeline' or 'sessions/2026-04-16-task'"
                            ),
                        },
                    },
                    "required": ["rel_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "wiki_write",
                "description": (
                    "Write or overwrite a wiki article at the given relative path. "
                    "Parent directories are created automatically. "
                    "The content should be valid Markdown with YAML frontmatter."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "rel_path": {
                            "type": "string",
                            "description": (
                                "Relative path inside the wiki root, without .md extension. "
                                "Example: 'architecture/pipeline'"
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "Full Markdown content to write (including frontmatter)",
                        },
                    },
                    "required": ["rel_path", "content"],
                },
            },
        },
    ]


def _safe_wiki_root(workspace_root: str) -> Path:
    root = _wiki_root(workspace_root)
    if not root.is_dir():
        raise ValueError(
            f"Wiki root does not exist: {root}. "
            "Run the pipeline at least once so the wiki is initialised."
        )
    return root


def _sanitise_rel_path(rel_path: str) -> str:
    cleaned = rel_path.strip().lstrip("/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[:-3]
    parts = Path(cleaned).parts
    if ".." in parts:
        raise ValueError(f"rel_path must not contain '..': {rel_path!r}")
    return cleaned


def handle_wiki_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    workspace_root: str,
) -> str:
    try:
        if tool_name == "wiki_search":
            return _wiki_search(arguments, workspace_root)
        if tool_name == "wiki_read":
            return _wiki_read(arguments, workspace_root)
        if tool_name == "wiki_write":
            return _wiki_write(arguments, workspace_root)
        return f"ERROR: unknown wiki tool: {tool_name!r}"
    except ValueError as exc:
        return f"ERROR: {exc}"
    except OSError as exc:
        logger.warning("wiki_tools: OS error in %s: %s", tool_name, exc)
        return f"ERROR: {exc}"
    except Exception as exc:
        logger.exception("wiki_tools: unexpected error in %s", tool_name)
        return f"ERROR: unexpected error — {exc}"


def _wiki_search(arguments: dict[str, Any], workspace_root: str) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required", "results": []}, ensure_ascii=False)
    top_k = int(arguments.get("top_k") or 5)
    top_k = max(1, min(top_k, 50))

    root = _safe_wiki_root(workspace_root)

    from backend.App.workspace.application.wiki_searcher import search

    hits = search(root, query, k=top_k)
    results = [
        {
            "rel_path": hit.chunk.rel_path,
            "section": hit.chunk.section,
            "text": hit.chunk.text,
            "score": round(hit.score, 4),
        }
        for hit in hits
    ]
    return json.dumps(results, ensure_ascii=False, indent=2)


def _wiki_read(arguments: dict[str, Any], workspace_root: str) -> str:
    rel_path_raw = str(arguments.get("rel_path") or "").strip()
    if not rel_path_raw:
        return "ERROR: rel_path is required"
    rel_path = _sanitise_rel_path(rel_path_raw)

    root = _safe_wiki_root(workspace_root)
    article_path = root / f"{rel_path}.md"
    if not article_path.is_file():
        return f"ERROR: article not found: {rel_path}"

    content = article_path.read_text(encoding="utf-8")
    if len(content) > _MAX_READ_CHARS:
        content = content[:_MAX_READ_CHARS] + f"\n…[truncated at {_MAX_READ_CHARS} chars]"
    return content


def _wiki_write(arguments: dict[str, Any], workspace_root: str) -> str:
    rel_path_raw = str(arguments.get("rel_path") or "").strip()
    if not rel_path_raw:
        return "ERROR: rel_path is required"
    content = str(arguments.get("content") or "")
    if not content.strip():
        return "ERROR: content must not be empty"
    rel_path = _sanitise_rel_path(rel_path_raw)

    root = _safe_wiki_root(workspace_root)
    article_path = root / f"{rel_path}.md"
    article_path.parent.mkdir(parents=True, exist_ok=True)
    article_path.write_text(content, encoding="utf-8")
    logger.info("wiki_tools: wrote %s (%d chars)", article_path, len(content))
    return f"OK: wrote {rel_path}.md ({len(content)} chars)"
