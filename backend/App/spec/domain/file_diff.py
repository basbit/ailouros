from __future__ import annotations

import re
from dataclasses import dataclass


class DiffParseError(ValueError):
    def __init__(self, line_no: int, message: str) -> None:
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no
        self.message = message


class DiffApplyError(ValueError):
    def __init__(self, hunk_index: int, message: str) -> None:
        super().__init__(f"hunk {hunk_index}: {message}")
        self.hunk_index = hunk_index
        self.message = message


@dataclass(frozen=True)
class DiffHunk:
    start_line: int
    end_line_exclusive: int
    replacement_lines: tuple[str, ...]


@dataclass(frozen=True)
class FileDiff:
    target_path: str
    hunks: tuple[DiffHunk, ...]


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER_A = re.compile(r"^--- ")
_FILE_HEADER_B = re.compile(r"^\+\+\+ (.+)")


def parse_unified_diff(text: str) -> FileDiff:
    lines = text.splitlines()
    target_path = ""
    hunks: list[DiffHunk] = []
    i = 0

    header_a_idx = next(
        (j for j, ln in enumerate(lines) if _FILE_HEADER_A.match(ln)), None
    )
    if header_a_idx is not None:
        i = header_a_idx + 1
        if i < len(lines):
            m = _FILE_HEADER_B.match(lines[i])
            if m:
                raw = m.group(1).strip()
                target_path = raw[2:] if raw.startswith("b/") else raw
                i += 1

    while i < len(lines):
        line = lines[i]
        hunk_match = _HUNK_HEADER.match(line)
        if not hunk_match:
            i += 1
            continue

        old_start = int(hunk_match.group(1))
        old_count_raw = hunk_match.group(2)
        old_count = int(old_count_raw) if old_count_raw is not None else 1

        line_no = i + 1
        i += 1

        start_line = old_start - 1
        end_line_exclusive = old_start - 1 + old_count
        replacement_lines: list[str] = []

        context_consumed = 0
        removed_consumed = 0

        while i < len(lines):
            current = lines[i]
            if _HUNK_HEADER.match(current):
                break
            if current.startswith("-"):
                removed_consumed += 1
                i += 1
            elif current.startswith("+"):
                replacement_lines.append(current[1:])
                i += 1
            elif current.startswith(" ") or current == "":
                context_consumed += 1
                replacement_lines.append(current[1:] if current.startswith(" ") else "")
                i += 1
            elif current.startswith("\\"):
                i += 1
            else:
                raise DiffParseError(i + 1, f"unexpected line prefix: {current[:1]!r}")

        expected_old = old_count
        actual_old = removed_consumed + context_consumed
        if actual_old != expected_old:
            raise DiffParseError(
                line_no,
                f"hunk header claims {expected_old} old lines but body has {actual_old}",
            )

        hunks.append(
            DiffHunk(
                start_line=start_line,
                end_line_exclusive=end_line_exclusive,
                replacement_lines=tuple(replacement_lines),
            )
        )

    return FileDiff(target_path=target_path, hunks=tuple(hunks))


def apply_diff(existing_text: str, diff: FileDiff) -> str:
    lines = existing_text.splitlines(keepends=True)
    n = len(lines)

    sorted_hunks = sorted(enumerate(diff.hunks), key=lambda t: t[1].start_line)

    result: list[str] = []
    cursor = 0

    for idx, hunk in sorted_hunks:
        if hunk.start_line < 0 or hunk.start_line > n:
            raise DiffApplyError(
                idx,
                f"start_line {hunk.start_line} is out of bounds for file with {n} lines",
            )
        if hunk.end_line_exclusive < hunk.start_line:
            raise DiffApplyError(
                idx,
                f"end_line_exclusive {hunk.end_line_exclusive} < start_line {hunk.start_line}",
            )
        if hunk.end_line_exclusive > n:
            raise DiffApplyError(
                idx,
                f"end_line_exclusive {hunk.end_line_exclusive} is out of bounds for file with {n} lines",
            )
        if hunk.start_line < cursor:
            raise DiffApplyError(
                idx,
                f"hunk start_line {hunk.start_line} overlaps already-applied region (cursor={cursor})",
            )

        result.extend(lines[cursor:hunk.start_line])

        for rep_line in hunk.replacement_lines:
            if rep_line.endswith("\n"):
                result.append(rep_line)
            else:
                result.append(rep_line + "\n")

        cursor = hunk.end_line_exclusive

    result.extend(lines[cursor:])

    if result and existing_text and not existing_text.endswith("\n"):
        if result[-1].endswith("\n"):
            result[-1] = result[-1].rstrip("\n")

    return "".join(result)


__all__ = [
    "DiffApplyError",
    "DiffHunk",
    "DiffParseError",
    "FileDiff",
    "apply_diff",
    "parse_unified_diff",
]
