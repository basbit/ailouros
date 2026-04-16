"""SmartContextBuilder — relevance-based context assembly within budget.

Instead of positional truncation (first-N-chars), ranks candidate sections by
cosine similarity to the step query, then fills the budget from most-relevant.

Toggle: SWARM_SMART_CONTEXT=1 (default off — positional truncation unchanged).
Falls back to positional ordering when embeddings are unavailable.

Usage::

    from backend.App.orchestration.application.smart_context_builder import build_context

    text = build_context(
        sections=[("Pattern memory", pattern_text), ("Wiki", wiki_text), ...],
        query="How does the auth module work?",
        budget_chars=8000,
    )
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------


def smart_context_enabled() -> bool:
    """Return True when SWARM_SMART_CONTEXT=1.

    Off by default; positional truncation is unchanged when unset.
    """
    # SWARM_SMART_CONTEXT: set to "1" to enable embedding-ranked context assembly.
    return os.getenv("SWARM_SMART_CONTEXT", "0").strip() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Embedding helpers (no numpy/scipy — pure Python)
# ---------------------------------------------------------------------------


def _get_provider() -> Optional[Any]:
    """Return the configured embedding provider, or None when unavailable.

    Follows the same import pattern as pattern_memory._get_provider() to stay
    consistent. Returns None if the provider is null or import fails.
    """
    try:
        from backend.App.integrations.infrastructure.embedding_service import (
            get_embedding_provider,
        )
    except ImportError:
        logger.warning(
            "smart_context_builder: embedding_service not importable; "
            "falling back to positional order"
        )
        return None
    provider = get_embedding_provider()
    if getattr(provider, "name", "") in ("null", "null+cache"):
        return None
    return provider


def _embed(provider: Any, text: str) -> list[float]:
    """Embed a single text string. Returns empty list on failure."""
    if not text.strip():
        return []
    try:
        vectors = provider.embed([text])
    except Exception as exc:
        logger.warning(
            "smart_context_builder: embed call failed (%s); "
            "falling back to positional order",
            exc,
        )
        return []
    if not vectors or not vectors[0]:
        return []
    return list(vectors[0])


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns 0.0 when either vector is empty or the lengths differ.
    Pure Python — no numpy/scipy.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _rank_sections(
    sections: list[tuple[str, str]],
    query_vec: list[float],
    provider: Any,
) -> list[tuple[str, str]]:
    """Return sections sorted by cosine similarity to query_vec (descending).

    Sections that cannot be embedded retain score 0.0 and appear last
    (preserving their relative positional order among equals).
    """
    scored: list[tuple[float, int, tuple[str, str]]] = []
    for idx, (label, text) in enumerate(sections):
        if not text.strip():
            continue
        sec_vec = _embed(provider, text)
        score = _cosine(query_vec, sec_vec) if sec_vec else 0.0
        scored.append((score, idx, (label, text)))
    # Sort by score descending; use original index as tiebreaker (stable).
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [sec for _, _, sec in scored]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context(
    sections: list[tuple[str, str]],
    query: str,
    budget_chars: int,
    *,
    separator: str = "\n\n",
) -> str:
    """Assemble context from sections ranked by relevance to query.

    Args:
        sections: List of (label, text) pairs. Label is prepended as a header.
        query: The step query used for ranking.
        budget_chars: Maximum total characters in the output.
        separator: String inserted between sections.

    Returns:
        Concatenated sections (most relevant first) up to budget_chars.
        Falls back to positional order when SWARM_SMART_CONTEXT is off or
        embeddings are unavailable.
    """
    # Filter empty sections up front; they're skipped regardless of path.
    non_empty = [(label, text) for label, text in sections if text.strip()]
    if not non_empty:
        return ""

    ranked = non_empty  # default: positional order

    if smart_context_enabled():
        provider = _get_provider()
        if provider is not None:
            query_vec = _embed(provider, query)
            if query_vec:
                try:
                    ranked = _rank_sections(non_empty, query_vec, provider)
                except Exception as exc:
                    logger.warning(
                        "smart_context_builder: ranking failed (%s); "
                        "falling back to positional order",
                        exc,
                    )
            else:
                logger.warning(
                    "smart_context_builder: query embedding returned empty vector; "
                    "falling back to positional order"
                )

    # Greedily fill budget from ranked sections; allow partial last section.
    parts: list[str] = []
    chars_used = 0
    sep_len = len(separator)

    for label, text in ranked:
        header = f"[{label}]\n" if label else ""
        block = header + text
        overhead = sep_len if parts else 0
        remaining = budget_chars - chars_used - overhead
        if remaining <= 0:
            break
        if len(block) <= remaining:
            parts.append(block)
            chars_used += overhead + len(block)
        else:
            # Partial: include as much of the block as the budget allows.
            parts.append(block[:remaining])
            break

    return separator.join(parts)


__all__ = [
    "build_context",
    "smart_context_enabled",
]
