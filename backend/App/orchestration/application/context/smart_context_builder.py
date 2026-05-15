from __future__ import annotations

import logging
import math
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def smart_context_enabled() -> bool:
    return os.getenv("SWARM_SMART_CONTEXT", "0").strip() in ("1", "true", "yes", "on")


def _get_provider() -> Optional[Any]:
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
    if not a or not b or len(a) != len(b):
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm_a * norm_b)


def _rank_sections(
    sections: list[tuple[str, str]],
    query_vec: list[float],
    provider: Any,
) -> list[tuple[str, str]]:
    scored: list[tuple[float, int, tuple[str, str]]] = []
    for idx, (label, text) in enumerate(sections):
        if not text.strip():
            continue
        sec_vec = _embed(provider, text)
        score = _cosine(query_vec, sec_vec) if sec_vec else 0.0
        scored.append((score, idx, (label, text)))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [sec for _, _, sec in scored]


def build_context(
    sections: list[tuple[str, str]],
    query: str,
    budget_chars: int,
    *,
    separator: str = "\n\n",
) -> str:
    non_empty = [(label, text) for label, text in sections if text.strip()]
    if not non_empty:
        return ""

    ranked = non_empty

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
            parts.append(block[:remaining])
            break

    return separator.join(parts)


__all__ = [
    "build_context",
    "smart_context_enabled",
]
