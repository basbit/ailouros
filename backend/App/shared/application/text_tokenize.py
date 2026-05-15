
from __future__ import annotations

import re

__all__ = ["tokenize_for_search"]

_SPLIT_RE = re.compile(r"[^\w]+")


def tokenize_for_search(text: str, *, min_len: int = 3) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    return [token for token in _SPLIT_RE.split(lowered) if len(token) >= min_len]
