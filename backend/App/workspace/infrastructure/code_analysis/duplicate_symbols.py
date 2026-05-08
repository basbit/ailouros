from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SymbolOccurrence:
    file: str
    kind: str
    line: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateSymbol:
    name: str
    kind: str
    occurrences: tuple[SymbolOccurrence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "occurrences": [item.to_dict() for item in self.occurrences],
        }


def find_duplicate_symbols(
    entities: Iterable[dict[str, Any]],
) -> list[DuplicateSymbol]:
    grouped: dict[tuple[str, str], list[SymbolOccurrence]] = defaultdict(list)
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name") or "").strip()
        kind = str(entity.get("kind") or "").strip().lower()
        path = str(entity.get("file") or entity.get("path") or "").strip()
        line_value = entity.get("line") or entity.get("start_line") or 0
        try:
            line_number = int(line_value)
        except (TypeError, ValueError):
            line_number = 0
        if not name or kind not in {"class", "function", "method", "interface", "type", "enum"}:
            continue
        grouped[(name, kind)].append(
            SymbolOccurrence(file=path, kind=kind, line=line_number)
        )
    duplicates: list[DuplicateSymbol] = []
    for (name, kind), occurrences in grouped.items():
        unique_files = {occurrence.file for occurrence in occurrences}
        if len(unique_files) <= 1:
            continue
        duplicates.append(
            DuplicateSymbol(
                name=name,
                kind=kind,
                occurrences=tuple(
                    sorted(occurrences, key=lambda item: (item.file, item.line))
                ),
            )
        )
    duplicates.sort(key=lambda dup: (dup.kind, dup.name))
    return duplicates


def summarize_duplicates(duplicates: list[DuplicateSymbol]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    for dup in duplicates:
        by_kind[dup.kind] = by_kind.get(dup.kind, 0) + 1
    return {
        "total": len(duplicates),
        "by_kind": by_kind,
    }
