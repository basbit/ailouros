from __future__ import annotations

import re

from backend.App.spec.domain.dsl_block import FencedDslBlock
from backend.App.spec.domain.dsl_registry import (
    DslFinding,
    DslParseResult,
)

_ITEM_PREFIX = "- "
_FIELD_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][\w]*)\s*:\s*(?P<value>.*)$")
_NAME_STYLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_PREDICATE_LEN = 500


class InvariantsParser:
    kind = "invariants"

    def parse(self, block: FencedDslBlock) -> DslParseResult:
        findings: list[DslFinding] = []
        entries, parse_findings = self._parse_entries(block)
        findings.extend(parse_findings)

        invariants: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for entry in entries:
            name = entry.fields.get("name", "").strip()
            predicate = entry.fields.get("predicate", "").strip()

            if not name:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message="invariants entry is missing 'name'.",
                        line_start=entry.line_start,
                    )
                )
                continue
            if not predicate:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=f"invariants entry {name!r} is missing 'predicate'.",
                        line_start=entry.line_start,
                    )
                )
                continue
            if name in seen_names:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=f"invariants entry name {name!r} is duplicated.",
                        line_start=entry.line_start,
                    )
                )
                continue
            seen_names.add(name)

            if not _NAME_STYLE_PATTERN.match(name):
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="warning",
                        message=(
                            f"invariants entry name {name!r} should be lower_snake_case "
                            "matching [a-z][a-z0-9_]*."
                        ),
                        line_start=entry.line_start,
                    )
                )
            if len(predicate) > _MAX_PREDICATE_LEN:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="warning",
                        message=(
                            f"invariants predicate {name!r} exceeds "
                            f"{_MAX_PREDICATE_LEN} characters; consider splitting."
                        ),
                        line_start=entry.line_start,
                    )
                )
            invariants.append({"name": name, "predicate": predicate})

        if not invariants and not any(f.severity == "error" for f in findings):
            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message="invariants block is empty; expected at least one '- name:' entry.",
                    line_start=block.line_start,
                )
            )

        return DslParseResult(
            kind=self.kind,
            payload={"invariants": invariants},
            findings=tuple(findings),
        )

    def _parse_entries(
        self, block: FencedDslBlock
    ) -> tuple[list["_Entry"], list[DslFinding]]:
        findings: list[DslFinding] = []
        entries: list[_Entry] = []
        current: _Entry | None = None
        for offset, raw in enumerate(block.content.splitlines()):
            stripped = raw.strip()
            absolute_line = block.line_start + offset + 1
            if not stripped:
                continue
            if stripped.startswith(_ITEM_PREFIX):
                if current is not None:
                    entries.append(current)
                current = _Entry(line_start=absolute_line, fields={})
                payload = stripped[len(_ITEM_PREFIX):].strip()
                if payload:
                    field_match = _FIELD_PATTERN.match(payload)
                    if field_match is None:
                        findings.append(
                            DslFinding(
                                kind=self.kind,
                                severity="error",
                                message=(
                                    f"invariants list item is not a key:value pair: {payload!r}"
                                ),
                                line_start=absolute_line,
                            )
                        )
                    else:
                        current.fields[field_match.group("key")] = self._strip_quotes(
                            field_match.group("value").strip()
                        )
                continue
            if current is None:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=(
                            f"invariants line is outside any '- name:' entry: {stripped!r}"
                        ),
                        line_start=absolute_line,
                    )
                )
                continue
            field_match = _FIELD_PATTERN.match(stripped)
            if field_match is None:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=f"invariants line is not a key:value pair: {stripped!r}",
                        line_start=absolute_line,
                    )
                )
                continue
            current.fields[field_match.group("key")] = self._strip_quotes(
                field_match.group("value").strip()
            )
        if current is not None:
            entries.append(current)
        return entries, findings

    def _strip_quotes(self, value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            return value[1:-1]
        return value


class _Entry:
    __slots__ = ("line_start", "fields")

    def __init__(self, line_start: int, fields: dict[str, str]) -> None:
        self.line_start = line_start
        self.fields = fields


__all__ = ["InvariantsParser"]
