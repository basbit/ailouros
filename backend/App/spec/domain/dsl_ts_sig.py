from __future__ import annotations

import re

from backend.App.spec.domain.dsl_block import FencedDslBlock
from backend.App.spec.domain.dsl_registry import (
    DslFinding,
    DslParseResult,
)

_FUNCTION_PATTERN = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s*\*?\s*"
    r"(?P<name>[A-Za-z_$][\w$]*)"
    r"\s*\((?P<params>.*?)\)"
    r"\s*:\s*(?P<returns>[^;{]+?)\s*;?\s*$"
)

_TYPE_ALIAS_PATTERN = re.compile(
    r"^(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*<[^>]*>)?\s*=\s*(?P<expression>.+?)\s*;?\s*$"
)

_CONST_PATTERN = re.compile(
    r"^(?:export\s+)?const\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"\s*:\s*(?P<type>[^=;]+?)\s*;?\s*$"
)

_INTERFACE_OPEN_PATTERN = re.compile(
    r"^(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"(?:\s*<[^>]*>)?(?:\s+extends\s+[^{]+)?\s*\{\s*$"
)

_INTERFACE_MEMBER_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_$][\w$]*)\??"
    r"\s*\((?P<params>.*?)\)"
    r"\s*:\s*(?P<returns>[^;]+?)\s*;?\s*$"
)


class TypeScriptSignatureParser:
    kind = "ts-sig"

    def parse(self, block: FencedDslBlock) -> DslParseResult:
        findings: list[DslFinding] = []
        functions: list[dict[str, object]] = []
        interfaces: list[dict[str, object]] = []
        types: list[dict[str, object]] = []
        constants: list[dict[str, object]] = []

        lines = block.content.splitlines()
        index = 0
        while index < len(lines):
            raw = lines[index]
            stripped = raw.strip()
            absolute_line = block.line_start + index + 1
            if not stripped:
                index += 1
                continue

            interface_open = _INTERFACE_OPEN_PATTERN.match(stripped)
            if interface_open is not None:
                interface, consumed, member_findings = self._consume_interface(
                    lines, index, block.line_start, interface_open.group("name")
                )
                if interface is not None:
                    interfaces.append(interface)
                findings.extend(member_findings)
                index += consumed
                continue

            function_match = _FUNCTION_PATTERN.match(stripped)
            if function_match is not None:
                functions.append(
                    {
                        "name": function_match.group("name"),
                        "params": self._parse_params(
                            function_match.group("params"), absolute_line, findings
                        ),
                        "returns": function_match.group("returns").strip(),
                    }
                )
                index += 1
                continue

            type_match = _TYPE_ALIAS_PATTERN.match(stripped)
            if type_match is not None:
                types.append(
                    {
                        "name": type_match.group("name"),
                        "expression": type_match.group("expression").strip(),
                    }
                )
                index += 1
                continue

            const_match = _CONST_PATTERN.match(stripped)
            if const_match is not None:
                constants.append(
                    {
                        "name": const_match.group("name"),
                        "type": const_match.group("type").strip(),
                    }
                )
                index += 1
                continue

            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message=f"ts-sig block has unrecognised TypeScript signature: {stripped!r}",
                    line_start=absolute_line,
                )
            )
            index += 1

        if not (functions or interfaces or types or constants) and not findings:
            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message="ts-sig block has no function, interface, type, or const declarations.",
                    line_start=block.line_start,
                )
            )

        return DslParseResult(
            kind=self.kind,
            payload={
                "functions": functions,
                "interfaces": interfaces,
                "types": types,
                "constants": constants,
            },
            findings=tuple(findings),
        )

    def _consume_interface(
        self,
        lines: list[str],
        open_index: int,
        block_line_start: int,
        name: str,
    ) -> tuple[dict[str, object] | None, int, list[DslFinding]]:
        findings: list[DslFinding] = []
        members: list[dict[str, object]] = []
        cursor = open_index + 1
        closed = False
        while cursor < len(lines):
            raw = lines[cursor]
            stripped = raw.strip()
            absolute_line = block_line_start + cursor + 1
            if stripped == "}":
                closed = True
                cursor += 1
                break
            if not stripped:
                cursor += 1
                continue
            member_match = _INTERFACE_MEMBER_PATTERN.match(stripped)
            if member_match is None:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=(
                            f"ts-sig interface {name!r} has unrecognised member: {stripped!r}"
                        ),
                        line_start=absolute_line,
                    )
                )
                cursor += 1
                continue
            members.append(
                {
                    "name": member_match.group("name"),
                    "params": self._parse_params(
                        member_match.group("params"), absolute_line, findings
                    ),
                    "returns": member_match.group("returns").strip(),
                }
            )
            cursor += 1
        if not closed:
            findings.append(
                DslFinding(
                    kind=self.kind,
                    severity="error",
                    message=f"ts-sig interface {name!r} is missing a closing brace.",
                    line_start=block_line_start + open_index + 1,
                )
            )
            return None, cursor - open_index, findings
        return (
            {"name": name, "members": members},
            cursor - open_index,
            findings,
        )

    def _parse_params(
        self,
        raw_params: str,
        absolute_line: int,
        findings: list[DslFinding],
    ) -> list[dict[str, str]]:
        text = raw_params.strip()
        if not text:
            return []
        parsed: list[dict[str, str]] = []
        for fragment in self._split_top_level_commas(text):
            piece = fragment.strip()
            if not piece:
                continue
            if ":" not in piece:
                findings.append(
                    DslFinding(
                        kind=self.kind,
                        severity="error",
                        message=f"ts-sig parameter is missing a type annotation: {piece!r}",
                        line_start=absolute_line,
                    )
                )
                continue
            name_part, type_part = piece.split(":", 1)
            parsed.append(
                {
                    "name": name_part.strip().lstrip("."),
                    "type": type_part.strip(),
                }
            )
        return parsed

    def _split_top_level_commas(self, text: str) -> list[str]:
        depth = 0
        buffer: list[str] = []
        out: list[str] = []
        for char in text:
            if char in "<([{":
                depth += 1
                buffer.append(char)
            elif char in ">)]}":
                depth = max(0, depth - 1)
                buffer.append(char)
            elif char == "," and depth == 0:
                out.append("".join(buffer))
                buffer = []
            else:
                buffer.append(char)
        if buffer:
            out.append("".join(buffer))
        return out


__all__ = ["TypeScriptSignatureParser"]
