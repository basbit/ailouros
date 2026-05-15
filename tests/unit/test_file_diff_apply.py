from __future__ import annotations

import pytest

from backend.App.spec.domain.file_diff import (
    DiffApplyError,
    DiffHunk,
    FileDiff,
    apply_diff,
    parse_unified_diff,
)

_EXISTING = "line1\nline2\nline3\nline4\nline5\n"


def _make_diff(hunks: list[DiffHunk]) -> FileDiff:
    return FileDiff(target_path="src/foo.py", hunks=tuple(hunks))


def test_apply_single_replacement() -> None:
    hunk = DiffHunk(start_line=1, end_line_exclusive=2, replacement_lines=("replaced\n",))
    result = apply_diff(_EXISTING, _make_diff([hunk]))
    lines = result.splitlines()
    assert lines[0] == "line1"
    assert lines[1] == "replaced"
    assert lines[2] == "line3"


def test_apply_deletion_only() -> None:
    hunk = DiffHunk(start_line=2, end_line_exclusive=3, replacement_lines=())
    result = apply_diff(_EXISTING, _make_diff([hunk]))
    lines = result.splitlines()
    assert "line3" not in lines
    assert len(lines) == 4


def test_apply_pure_insertion() -> None:
    hunk = DiffHunk(start_line=2, end_line_exclusive=2, replacement_lines=("inserted\n",))
    result = apply_diff(_EXISTING, _make_diff([hunk]))
    lines = result.splitlines()
    assert lines[2] == "inserted"
    assert lines[3] == "line3"
    assert len(lines) == 6


def test_apply_multiple_hunks_ordered() -> None:
    h1 = DiffHunk(start_line=0, end_line_exclusive=1, replacement_lines=("FIRST\n",))
    h2 = DiffHunk(start_line=4, end_line_exclusive=5, replacement_lines=("LAST\n",))
    result = apply_diff(_EXISTING, _make_diff([h1, h2]))
    lines = result.splitlines()
    assert lines[0] == "FIRST"
    assert lines[-1] == "LAST"


def test_apply_start_line_out_of_bounds_raises() -> None:
    hunk = DiffHunk(start_line=99, end_line_exclusive=100, replacement_lines=("x\n",))
    with pytest.raises(DiffApplyError) as exc_info:
        apply_diff(_EXISTING, _make_diff([hunk]))
    assert exc_info.value.hunk_index == 0


def test_apply_end_line_out_of_bounds_raises() -> None:
    hunk = DiffHunk(start_line=3, end_line_exclusive=99, replacement_lines=("x\n",))
    with pytest.raises(DiffApplyError) as exc_info:
        apply_diff(_EXISTING, _make_diff([hunk]))
    assert exc_info.value.hunk_index == 0


def test_apply_overlapping_hunks_raises() -> None:
    h1 = DiffHunk(start_line=0, end_line_exclusive=3, replacement_lines=("a\n",))
    h2 = DiffHunk(start_line=2, end_line_exclusive=4, replacement_lines=("b\n",))
    with pytest.raises(DiffApplyError) as exc_info:
        apply_diff(_EXISTING, _make_diff([h1, h2]))
    assert exc_info.value.hunk_index == 1


def test_apply_diff_error_carries_hunk_index() -> None:
    hunk = DiffHunk(start_line=50, end_line_exclusive=51, replacement_lines=("x\n",))
    with pytest.raises(DiffApplyError) as exc_info:
        apply_diff(_EXISTING, _make_diff([hunk]))
    assert exc_info.value.hunk_index == 0
    assert exc_info.value.message


def test_apply_round_trip_via_parse() -> None:
    original = "alpha\nbeta\ngamma\n"
    diff_text = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -2,1 +2,1 @@\n"
        "-beta\n"
        "+BETA\n"
    )
    parsed = parse_unified_diff(diff_text)
    result = apply_diff(original, parsed)
    assert result == "alpha\nBETA\ngamma\n"


def test_apply_no_hunks_returns_unchanged() -> None:
    result = apply_diff(_EXISTING, _make_diff([]))
    assert result == _EXISTING


def test_apply_end_line_less_than_start_raises() -> None:
    hunk = DiffHunk(start_line=3, end_line_exclusive=1, replacement_lines=())
    with pytest.raises(DiffApplyError):
        apply_diff(_EXISTING, _make_diff([hunk]))
