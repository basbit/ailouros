from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.domain.pipeline_step_catalog import wiki_loader_config

logger = logging.getLogger(__name__)

_WIKI_CONTEXT_CONFIG = wiki_loader_config()

_MAX_INDEX_CHARS: int = int(_WIKI_CONTEXT_CONFIG["max_index_chars"])
_MAX_ARTICLE_CHARS: int = int(_WIKI_CONTEXT_CONFIG["max_article_chars"])
_MAX_TOTAL_CHARS: int = int(_WIKI_CONTEXT_CONFIG["max_total_chars"])
_QUERY_MAX_CHARS: int = int(_WIKI_CONTEXT_CONFIG["query_max_chars"])
_PRIORITY_DIRS: tuple[str, ...] = tuple(_WIKI_CONTEXT_CONFIG["priority_dirs"])
_STEP_HINTS: dict[str, str] = dict(_WIKI_CONTEXT_CONFIG["step_hints"])


def _resolve_query_sources_per_step(cfg: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    groups = cfg.get("query_source_groups") or {}
    if not isinstance(groups, dict):
        groups = {}
    per_step = cfg.get("query_sources_per_step") or {}
    out: dict[str, tuple[str, ...]] = {}
    for step, sources in per_step.items():
        if isinstance(sources, str) and sources.startswith("@") and sources[1:] in groups:
            g = groups[sources[1:]]
            out[str(step)] = tuple(str(x) for x in g) if isinstance(g, list) else ()
        elif isinstance(sources, list):
            out[str(step)] = tuple(str(x) for x in sources)
        else:
            out[str(step)] = ()
    return out


_QUERY_SOURCES_PER_STEP: dict[str, tuple[str, ...]] = _resolve_query_sources_per_step(_WIKI_CONTEXT_CONFIG)
_DEFAULT_QUERY_SOURCES: tuple[str, ...] = tuple(_WIKI_CONTEXT_CONFIG["default_query_sources"])


def _read_file(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text
    except OSError:
        return ""


def _flat_dump(wiki_root: Path) -> str:
    parts: list[str] = []
    total = 0

    index_path = wiki_root / "index.md"
    if index_path.exists():
        content = _read_file(index_path, _MAX_INDEX_CHARS)
        if content:
            parts.append(f"## index\n{content}")
            total += len(content)

    for subdirectory in _PRIORITY_DIRS:
        if total >= _MAX_TOTAL_CHARS:
            break
        subdirectory_path = wiki_root / subdirectory
        if not subdirectory_path.is_dir():
            continue
        for md_file in sorted(subdirectory_path.glob("*.md"))[:4]:
            if total >= _MAX_TOTAL_CHARS:
                break
            content = _read_file(md_file, _MAX_ARTICLE_CHARS)
            if content:
                rel_path = md_file.relative_to(wiki_root).with_suffix("").as_posix()
                parts.append(f"## {rel_path}\n{content}")
                total += len(content)

    if not parts:
        return ""

    logger.debug("wiki_context_loader: flat dump %d chars from %s", total, wiki_root)
    return "\n\n".join(parts)


def load_wiki_context(
    workspace_root: str | Path,
    *,
    query: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> str:
    wiki_root = Path(workspace_root) / ".swarm" / "wiki"
    if not wiki_root.exists():
        return ""

    if query and query.strip():
        try:
            from backend.App.workspace.application.wiki_searcher import (
                search_block,
                wiki_search_enabled,
            )
        except ImportError as exc:
            raise RuntimeError(
                f"wiki_searcher is not available — cannot perform semantic wiki lookup: {exc}"
            ) from exc
        if wiki_search_enabled():
            semantic_block = search_block(wiki_root, query, max_chars=max_chars)
            if semantic_block:
                return semantic_block

    return _flat_dump(wiki_root)


def query_for_pipeline_step(
    state: Mapping[str, Any] | None, step_id: str
) -> str:
    if not isinstance(state, Mapping):
        return ""

    sources = _QUERY_SOURCES_PER_STEP.get(step_id, _DEFAULT_QUERY_SOURCES)
    body = ""
    for key in sources:
        value = str(state.get(key) or "").strip()
        if value:
            body = value
            break

    if not body:
        for key, value in state.items():
            if not key.endswith("_output") or not isinstance(value, str):
                continue
            value = value.strip()
            if value:
                body = value
                break

    if not body:
        return ""

    hint = _STEP_HINTS.get(step_id, "")
    query = f"{hint}\n{body}".strip() if hint else body
    if len(query) > _QUERY_MAX_CHARS:
        query = query[:_QUERY_MAX_CHARS]
    return query
