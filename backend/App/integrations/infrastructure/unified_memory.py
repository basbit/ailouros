from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "unified_memory_search_enabled",
    "search_memory",
    "format_unified_memory_block",
]


def unified_memory_search_enabled() -> bool:
    return os.getenv("SWARM_UNIFIED_MEMORY_SEARCH", "1").strip() not in ("0", "false", "no", "off")


def _topk() -> int:
    raw = os.getenv("SWARM_UNIFIED_MEMORY_TOPK", "4").strip()
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return 4


def _max_chars() -> int:
    raw = os.getenv("SWARM_UNIFIED_MEMORY_MAX_CHARS", "6000").strip()
    try:
        return max(200, int(raw))
    except (ValueError, TypeError):
        return 6000


class _MemHit:
    __slots__ = ("source", "label", "body", "score")

    def __init__(self, source: str, label: str, body: str, score: float) -> None:
        self.source = source
        self.label = label
        self.body = body
        self.score = score


def _query_pattern_memory(state: Mapping[str, Any], query: str, limit: int) -> list[_MemHit]:
    try:
        from backend.App.integrations.infrastructure.pattern_memory import search_patterns
        hits = search_patterns(state, query, limit=limit)
        return [
            _MemHit(source="pattern", label=key, body=value, score=score)
            for key, value, score in hits
        ]
    except Exception as exc:
        logger.warning("unified_memory: pattern_memory query failed: %s", exc)
        return []


def _query_cross_task_memory(state: Mapping[str, Any], query: str, limit: int) -> list[_MemHit]:
    try:
        from backend.App.integrations.infrastructure.cross_task_memory import search_episodes
        hits = search_episodes(state, query, limit=limit)
        results = []
        for episode, score in hits:
            step = str(episode.get("step") or "?")
            tid = str(episode.get("task_id") or "")[:8]
            body = str(episode.get("body") or "").strip()
            label = f"step={step} task={tid}…"
            results.append(_MemHit(source="episode", label=label, body=body, score=score))
        return results
    except Exception as exc:
        logger.warning("unified_memory: cross_task_memory query failed: %s", exc)
        return []


def _query_wiki(state: Mapping[str, Any], query: str, limit: int) -> list[_MemHit]:
    wiki_root = str(state.get("wiki_root") or state.get("workspace_root") or "").strip()
    if not wiki_root:
        return []
    try:
        from backend.App.workspace.application.wiki_searcher import search as wiki_search
        wiki_hits = wiki_search(wiki_root, query, k=limit)
        return [
            _MemHit(
                source="wiki",
                label=f"{h.chunk.rel_path}#{h.chunk.section}",
                body=h.chunk.text,
                score=h.score,
            )
            for h in wiki_hits
        ]
    except Exception as exc:
        logger.warning("unified_memory: wiki query failed: %s", exc)
        return []


def search_memory(
    state: Mapping[str, Any],
    query: str,
    *,
    limit: Optional[int] = None,
) -> list[_MemHit]:
    if not unified_memory_search_enabled():
        return []
    k = limit if limit is not None else _topk()
    hits: list[_MemHit] = []
    hits.extend(_query_pattern_memory(state, query, k))
    hits.extend(_query_cross_task_memory(state, query, k))
    hits.extend(_query_wiki(state, query, k))
    hits.sort(key=lambda h: -h.score)
    return hits


def format_unified_memory_block(
    state: Mapping[str, Any],
    query: str,
    *,
    max_chars: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    budget = max_chars if max_chars is not None else _max_chars()
    if budget <= 0:
        return ""
    hits = search_memory(state, query, limit=limit)
    if not hits:
        return ""

    source_labels = {"pattern": "Pattern", "episode": "Episode", "wiki": "Wiki"}
    lines = ["[Unified memory — relevant context from past runs, patterns, wiki]\n"]
    total = len(lines[0])
    for hit in hits:
        src = source_labels.get(hit.source, hit.source)
        body_preview = hit.body.strip()[:600]
        chunk = (
            f"### [{src}] {hit.label} (score={hit.score:.2f})\n"
            f"{body_preview}\n\n"
        )
        if total + len(chunk) > budget:
            break
        lines.append(chunk)
        total += len(chunk)

    if len(lines) == 1:
        return ""
    return "".join(lines).rstrip() + "\n\n"
