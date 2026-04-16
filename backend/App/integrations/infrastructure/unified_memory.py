"""H-9 — UnifiedMemorySearch.

Single fan-out query across all memory backends that now share the same
embedding dimension (via the shared embedding_service provider):

    PatternMemory  → key/value patterns stored by the swarm (search_patterns)
    CrossTaskMemory→ compressed episode logs from previous runs (search_episodes)
    wiki_searcher  → paragraph-level chunks from workspace wiki (search)

Results are merged by hybrid score and returned as a single compact block
ready for prompt injection.  Each backend is queried independently; if one
is disabled or unavailable, the others continue normally.

Env vars
--------
SWARM_UNIFIED_MEMORY_SEARCH=1  (default ON) — master toggle.
SWARM_UNIFIED_MEMORY_TOPK      — results per backend (default 4).
SWARM_UNIFIED_MEMORY_MAX_CHARS — total block budget (default 6000).
"""
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
    """Return True when SWARM_UNIFIED_MEMORY_SEARCH is not explicitly disabled."""
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


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class _MemHit:
    """Unified hit from any backend."""
    __slots__ = ("source", "label", "body", "score")

    def __init__(self, source: str, label: str, body: str, score: float) -> None:
        self.source = source   # "pattern" | "episode" | "wiki"
        self.label = label     # display label (key, step+task_id, rel_path)
        self.body = body       # text content
        self.score = score


# ---------------------------------------------------------------------------
# Backend adapters — each returns list[_MemHit], never raises
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_memory(
    state: Mapping[str, Any],
    query: str,
    *,
    limit: Optional[int] = None,
) -> list[_MemHit]:
    """Fan out to all memory backends and return a merged, score-ranked list.

    Args:
        state: Pipeline state (provides agent_config, workspace_root, etc.).
        query: Free-text search query.
        limit: Max results per backend; defaults to SWARM_UNIFIED_MEMORY_TOPK.

    Returns:
        List of :class:`_MemHit` sorted by descending score.  Empty when
        unified memory search is disabled or no backends returned results.
    """
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
    """Return a prompt-ready block merging results from all memory backends.

    Returns empty string when unified memory search is disabled, no results
    are found, or *max_chars* is zero.

    Args:
        state:      Pipeline state dict.
        query:      Search query (typically the user task or step description).
        max_chars:  Total character budget for the block.  Defaults to
                    SWARM_UNIFIED_MEMORY_MAX_CHARS.
        limit:      Per-backend result limit; defaults to SWARM_UNIFIED_MEMORY_TOPK.
    """
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

    if len(lines) == 1:  # only header, no results fit
        return ""
    return "".join(lines).rstrip() + "\n\n"
