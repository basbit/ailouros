"""Text tokenisation helpers for lexical search / scoring.

These are deliberately domain-agnostic and live in ``shared`` so that every
memory service (pattern memory, cross-task memory, etc.) uses the exact same
tokenisation rules. Previously the logic was duplicated verbatim across
``integrations/infrastructure/pattern_memory.py`` and
``integrations/infrastructure/cross_task_memory.py``.
"""

from __future__ import annotations

import re

__all__ = ["tokenize_for_search"]


_SPLIT_RE = re.compile(r"[^\w]+")


def tokenize_for_search(text: str, *, min_len: int = 3) -> list[str]:
    """Split ``text`` into lowercase word tokens suitable for lexical scoring.

    Args:
        text: Input string. ``None``-like values are treated as empty.
        min_len: Minimum token length to keep. Tokens shorter than this are
            discarded to reduce noise from stop-words and 1–2 letter tokens.

    Returns:
        A list of lowercase tokens (duplicates preserved in order). Returns an
        empty list for empty/whitespace-only input.
    """
    if not text:
        return []
    lowered = text.lower()
    return [token for token in _SPLIT_RE.split(lowered) if len(token) >= min_len]
