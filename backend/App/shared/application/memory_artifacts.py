
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


_BA_ARCH_BUILDER = MemoryArtifactBuilder()

_PM_BUILDER = MemoryArtifactBuilder(
    max_items=6,
    strip_numeric_list_prefix=True,
    drop_code_fences=False,
    drop_json_like=False,
)


def repo_evidence_verified_facts(
    repo_evidence: Sequence[Mapping[str, Any]] | list[dict[str, Any]],
) -> list[str]:
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
