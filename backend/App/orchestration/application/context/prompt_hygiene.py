
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

DEFAULT_MAX_HEAD_CHARS = 4096


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
        re.compile(
            r"\b(?:ts|time|timestamp|at|generated)\s*[:=]\s*1[0-9]{9}\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class VolatileMatch:

    label: str  # stable rule name (e.g. ``iso_datetime``)
    offset: int  # 0-based character index of the match inside the head slice
    snippet: str  # up to 80 chars around the match, for humans


def detect_volatile_head(
    prompt: str,
    max_head_chars: int = DEFAULT_MAX_HEAD_CHARS,
) -> list[VolatileMatch]:
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
