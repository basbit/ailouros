"""Shared helpers for building per-role memory artefacts.

Orchestration's PM / BA / Architect nodes all emit structured "memory
artefact" blocks (``{verified_facts, hypotheses, decisions, dead_ends,
constraints}``). Three near-identical line-compaction helpers lived in
``nodes/pm.py``, ``nodes/ba.py`` and ``nodes/arch.py`` differing only in
which lines they filter (PM strips numeric list prefixes; BA/Architect drop
markdown fences and JSON-looking lines).

This module expresses the common compaction as a single
:class:`MemoryArtifactBuilder` that takes a filter configuration. Backwards
compatible wrappers are exposed in
``orchestration/application/nodes/_shared.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MemoryArtifactBuilder",
    "repo_evidence_verified_facts",
]


@dataclass(frozen=True)
class MemoryArtifactBuilder:
    """Configurable line compaction for role memory artefacts.

    Attributes:
        max_items: Maximum number of lines to keep.
        max_chars: Truncate lines longer than this (adds an ellipsis).
        strip_numeric_list_prefix: If ``True``, removes a leading
            ``"1. "`` / ``"2) "``-style prefix (PM-style).
        drop_code_fences: If ``True``, drops lines that start with triple
            backticks.
        drop_json_like: If ``True``, drops lines that start with ``{``
            (typically serialised artefacts that we don't want to echo
            back into memory).
    """

    max_items: int = 4
    max_chars: int = 180
    strip_numeric_list_prefix: bool = False
    drop_code_fences: bool = True
    drop_json_like: bool = True

    def compact(self, raw: str) -> list[str]:
        items: list[str] = []
        for line in (raw or "").splitlines():
            text = line.strip().lstrip("-*# ").strip()
            if not text:
                continue
            if self.strip_numeric_list_prefix and self._looks_numeric(text):
                text = text.split(".", 1)[1].strip()
            if self.drop_code_fences and text.startswith("```"):
                continue
            if self.drop_json_like and text.startswith("{"):
                continue
            if not text or text in items:
                continue
            if len(text) > self.max_chars:
                text = text[: self.max_chars - 1].rstrip() + "…"
            items.append(text)
            if len(items) >= self.max_items:
                break
        return items

    @staticmethod
    def _looks_numeric(text: str) -> bool:
        return text[:2].isdigit() and "." in text[:4]


# Preset used by BA / Architect nodes — filters code fences and JSON lines,
# no numeric-list stripping, 4 items, 180 chars.
_BA_ARCH_BUILDER = MemoryArtifactBuilder()


# Preset used by PM node — strips numeric list prefixes ("1. foo" -> "foo"),
# allows code fences through (PM output is prose with numbered items).
_PM_BUILDER = MemoryArtifactBuilder(
    max_items=6,
    strip_numeric_list_prefix=True,
    drop_code_fences=False,
    drop_json_like=False,
)


def repo_evidence_verified_facts(
    repo_evidence: Sequence[Mapping[str, Any]] | list[dict[str, Any]],
) -> list[str]:
    """Turn repo-evidence artefacts into at most 6 deduped ``"why (path)"`` lines."""
    facts: list[str] = []
    for item in repo_evidence:
        path = str(item.get("path") or "").strip()
        why = str(item.get("why") or "").strip()
        if not why:
            continue
        text = why if not path else f"{why} ({path})"
        if text not in facts:
            facts.append(text)
    return facts[:6]
