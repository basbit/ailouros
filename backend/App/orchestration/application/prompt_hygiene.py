"""Prompt-head hygiene checks (§23.4).

Local loaders (LM Studio, llama.cpp, Ollama) keep a **slot prefix cache**:
when an inbound request shares its first N tokens with the last one,
prefill for those tokens is reused — which can turn a 5 s prefill into
effectively 0 s. The cache is invalidated the moment a differing byte
appears, so putting volatile content (ISO-8601 timestamps, UUIDs,
``task_id=…``, ``X-Request-Id``) near the top of the prompt silently
destroys the cache every call.

This module provides a cheap detector for those patterns. Intended uses:

1. **Tests / CI lint**: assert that a freshly built prompt has no
   volatile content in its first ``max_head_chars`` (default 4 KB).
   Use :func:`assert_prompt_head_stable`.
2. **Runtime diagnostics**: call :func:`detect_volatile_head` manually
   when investigating "why is my first-call prefill always slow".
   The function is pure (no side effects) and safe to call repeatedly.

The patterns cover the common offenders:

* ``1970-01-01T00:00:00Z``          — ISO-8601 with a ``T``
* ``2026-04-16 12:34:56``           — space-separated date-time
* ``UUID `` / ``uuid=`` / bare UUID — ``xxxxxxxx-xxxx-xxxx-…``
* ``task_id=abcd1234``              — correlation ids
* ``X-Request-Id: …``               — HTTP correlation headers
* ``<!-- built 12:34 -->``          — build timestamp comments
* ``elapsed=12.34s``                — per-call timing
* ``nonce=…`` / ``salt=…`` / ``now=…``

The detector intentionally errs toward **false positives** — a legitimate
timestamp in context ("fix this commit from 2026-04-15") would fire, but
such cases should live well below the 4 KB head boundary anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

DEFAULT_MAX_HEAD_CHARS = 4096


# Each entry is (label, compiled-regex). Names are stable — tests assert
# on them when reporting which rule fired.
_VOLATILE_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    (
        "iso_datetime",
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
        ),
    ),
    (
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    (
        "task_id_correlation",
        re.compile(r"\btask[_ -]?id\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    ),
    (
        "request_id_header",
        re.compile(r"\bx[- ]?request[- ]?id\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    ),
    (
        "elapsed_timing",
        re.compile(r"\belapsed\s*[:=]\s*[0-9]+(?:\.[0-9]+)?\s*(?:ms|s|sec|seconds)?\b", re.IGNORECASE),
    ),
    (
        "nonce_like",
        re.compile(r"\b(?:nonce|salt|rand|random|now)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
    ),
    (
        "epoch_seconds",
        # 10-digit epoch timestamps (post-2001). Avoid matching plain
        # integers that happen to be this long — require the word to be
        # introduced by "ts", "time", "timestamp", "at", or "generated".
        re.compile(
            r"\b(?:ts|time|timestamp|at|generated)\s*[:=]\s*1[0-9]{9}\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class VolatileMatch:
    """A single volatile-pattern hit found in the prompt head."""

    label: str  # stable rule name (e.g. ``iso_datetime``)
    offset: int  # 0-based character index of the match inside the head slice
    snippet: str  # up to 80 chars around the match, for humans


def detect_volatile_head(
    prompt: str,
    max_head_chars: int = DEFAULT_MAX_HEAD_CHARS,
) -> list[VolatileMatch]:
    """Scan the first *max_head_chars* of *prompt* for volatile patterns.

    Returns an empty list when the head is clean.  Order of returned
    matches is ``(offset, label)`` — deterministic for stable test
    assertions.
    """
    if not prompt:
        return []
    head = prompt[:max_head_chars]
    matches: list[VolatileMatch] = []
    for label, pattern in _VOLATILE_PATTERNS:
        for m in pattern.finditer(head):
            start = max(0, m.start() - 20)
            end = min(len(head), m.end() + 20)
            matches.append(
                VolatileMatch(
                    label=label,
                    offset=m.start(),
                    snippet=head[start:end].replace("\n", " "),
                )
            )
    matches.sort(key=lambda v: (v.offset, v.label))
    return matches


def assert_prompt_head_stable(
    prompt: str,
    *,
    max_head_chars: int = DEFAULT_MAX_HEAD_CHARS,
    context: str = "prompt",
) -> None:
    """Raise :class:`AssertionError` when *prompt* head has volatile bytes.

    Intended for tests — production code should either tolerate the
    warning or call :func:`detect_volatile_head` directly.
    """
    hits = detect_volatile_head(prompt, max_head_chars=max_head_chars)
    if not hits:
        return
    lines = [
        f"{context}: volatile content in first {max_head_chars} chars "
        f"(breaks local-loader slot cache). Move it below or strip it:",
    ]
    for h in hits[:10]:  # cap output — massive lists aren't useful
        lines.append(f"  - {h.label} @ offset {h.offset}: {h.snippet!r}")
    raise AssertionError("\n".join(lines))


__all__ = [
    "DEFAULT_MAX_HEAD_CHARS",
    "VolatileMatch",
    "assert_prompt_head_stable",
    "detect_volatile_head",
]
