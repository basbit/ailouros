from __future__ import annotations

import re
from dataclasses import dataclass

_BEGIN_PATTERN = re.compile(r"#\s*@spec-keep\s+begin\s+(.+)$")
_END_PATTERN = re.compile(r"#\s*@spec-keep\s+end\s*$")


class KeepRegionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedRegion:
    reason: str
    content: str
    begin_index: int
    end_index: int


def extract_keep_regions(text: str) -> tuple[ExtractedRegion, ...]:
    lines = text.splitlines(keepends=True)
    regions: list[ExtractedRegion] = []
    open_reason: str | None = None
    open_start: int | None = None
    open_lines: list[str] = []

    for idx, line in enumerate(lines):
        if _BEGIN_PATTERN.search(line):
            if open_reason is not None:
                previous_begin_line = (open_start or 0) + 1
                raise KeepRegionError(
                    f"nested @spec-keep begin at line {idx + 1}; "
                    f"previous begin at line {previous_begin_line} never closed"
                )
            match = _BEGIN_PATTERN.search(line)
            assert match is not None
            open_reason = match.group(1).strip()
            open_start = idx
            open_lines = [line]
        elif _END_PATTERN.search(line):
            if open_reason is None or open_start is None:
                raise KeepRegionError(
                    f"@spec-keep end at line {idx + 1} has no matching begin"
                )
            open_lines.append(line)
            regions.append(
                ExtractedRegion(
                    reason=open_reason,
                    content="".join(open_lines),
                    begin_index=open_start,
                    end_index=idx,
                )
            )
            open_reason = None
            open_start = None
            open_lines = []
        elif open_reason is not None:
            open_lines.append(line)

    if open_reason is not None:
        unclosed_line = (open_start or 0) + 1
        raise KeepRegionError(
            f"@spec-keep begin {open_reason!r} at line {unclosed_line} was never closed"
        )

    return tuple(regions)


def apply_keep_regions(existing_text: str, new_text: str) -> str:
    if not existing_text:
        return new_text

    regions = extract_keep_regions(existing_text)
    if not regions:
        return new_text

    new_lines = new_text.splitlines(keepends=True)
    result_lines: list[str] = list(new_lines)

    for region in regions:
        inserted = False
        for idx, line in enumerate(result_lines):
            if _BEGIN_PATTERN.search(line):
                match = _BEGIN_PATTERN.search(line)
                assert match is not None
                if match.group(1).strip() == region.reason:
                    end_idx = idx
                    for j in range(idx + 1, len(result_lines)):
                        if _END_PATTERN.search(result_lines[j]):
                            end_idx = j
                            break
                    region_lines = region.content.splitlines(keepends=True)
                    result_lines[idx:end_idx + 1] = region_lines
                    inserted = True
                    break
        if not inserted:
            if result_lines and not result_lines[-1].endswith("\n"):
                result_lines[-1] += "\n"
            result_lines.extend(region.content.splitlines(keepends=True))

    return "".join(result_lines)


__all__ = [
    "ExtractedRegion",
    "KeepRegionError",
    "apply_keep_regions",
    "extract_keep_regions",
]
