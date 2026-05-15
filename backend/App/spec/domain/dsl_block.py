from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FencedDslBlock:
    kind: str
    language: str
    content: str
    line_start: int


_FENCE_OPEN_PATTERN = re.compile(
    r"^```(?P<lang>[\w+\-]*)\s*\{(?P<attrs>[^}]*)\}\s*$",
    re.MULTILINE,
)
_FENCE_CLOSE_LINE = "```"
_DSL_ATTR_PATTERN = re.compile(r"dsl\s*=\s*(?P<kind>[\w+\-]+)")


def _extract_dsl_kind(attribute_text: str) -> str:
    match = _DSL_ATTR_PATTERN.search(attribute_text)
    if match is None:
        return ""
    return match.group("kind").strip()


def extract_dsl_blocks(markdown: str) -> tuple[FencedDslBlock, ...]:
    if not markdown:
        return ()
    lines = markdown.splitlines()
    blocks: list[FencedDslBlock] = []
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        match = _FENCE_OPEN_PATTERN.match(line)
        if match is None:
            line_index += 1
            continue
        kind = _extract_dsl_kind(match.group("attrs"))
        if not kind:
            line_index += 1
            continue
        language = match.group("lang") or ""
        content_lines: list[str] = []
        cursor = line_index + 1
        while cursor < len(lines):
            inner = lines[cursor]
            if inner.strip() == _FENCE_CLOSE_LINE:
                break
            content_lines.append(inner)
            cursor += 1
        blocks.append(
            FencedDslBlock(
                kind=kind,
                language=language,
                content="\n".join(content_lines),
                line_start=line_index + 1,
            )
        )
        line_index = cursor + 1
    return tuple(blocks)


def filter_by_kind(blocks: Iterable[FencedDslBlock], kind: str) -> tuple[FencedDslBlock, ...]:
    return tuple(block for block in blocks if block.kind == kind)


__all__ = ["FencedDslBlock", "extract_dsl_blocks", "filter_by_kind"]
