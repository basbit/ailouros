from __future__ import annotations

import pytest

from backend.App.spec.domain.file_diff import DiffHunk, DiffParseError, FileDiff, parse_unified_diff

_SIMPLE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2_updated
 line3
"""

_MULTI_HUNK_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,2 +1,2 @@
-alpha
+ALPHA
 beta
@@ -5,2 +5,2 @@
 delta
-epsilon
+EPSILON
"""

_INSERTION_ONLY_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -2,0 +3,2 @@
+inserted_a
+inserted_b
"""

_NO_HEADER_DIFF = """\
@@ -1,1 +1,1 @@
-old
+new
"""


def test_parse_simple_diff_path() -> None:
    result = parse_unified_diff(_SIMPLE_DIFF)
    assert result.target_path == "src/foo.py"


def test_parse_simple_diff_one_hunk() -> None:
    result = parse_unified_diff(_SIMPLE_DIFF)
    assert len(result.hunks) == 1


def test_parse_simple_diff_hunk_bounds() -> None:
    result = parse_unified_diff(_SIMPLE_DIFF)
    hunk = result.hunks[0]
    assert hunk.start_line == 0
    assert hunk.end_line_exclusive == 3


def test_parse_simple_diff_replacement_lines() -> None:
    result = parse_unified_diff(_SIMPLE_DIFF)
    hunk = result.hunks[0]
    assert "line1" in hunk.replacement_lines[0]
    assert "line2_updated" in hunk.replacement_lines[1]
    assert "line3" in hunk.replacement_lines[2]


def test_parse_multi_hunk_count() -> None:
    result = parse_unified_diff(_MULTI_HUNK_DIFF)
    assert len(result.hunks) == 2


def test_parse_multi_hunk_first_bounds() -> None:
    result = parse_unified_diff(_MULTI_HUNK_DIFF)
    assert result.hunks[0].start_line == 0
    assert result.hunks[0].end_line_exclusive == 2


def test_parse_multi_hunk_second_bounds() -> None:
    result = parse_unified_diff(_MULTI_HUNK_DIFF)
    assert result.hunks[1].start_line == 4
    assert result.hunks[1].end_line_exclusive == 6


def test_parse_no_file_header_still_parses() -> None:
    result = parse_unified_diff(_NO_HEADER_DIFF)
    assert len(result.hunks) == 1
    assert result.hunks[0].start_line == 0
    assert result.hunks[0].end_line_exclusive == 1


def test_parse_empty_string_returns_empty_diff() -> None:
    result = parse_unified_diff("")
    assert result.hunks == ()
    assert result.target_path == ""


def test_parse_returns_frozen_dataclass() -> None:
    result = parse_unified_diff(_SIMPLE_DIFF)
    assert isinstance(result, FileDiff)
    assert isinstance(result.hunks[0], DiffHunk)


def test_malformed_hunk_body_line_count_raises() -> None:
    bad_diff = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,3 @@
-only_one_line_removed
+replacement
"""
    with pytest.raises(DiffParseError) as exc_info:
        parse_unified_diff(bad_diff)
    assert exc_info.value.line_no > 0


def test_diff_parse_error_carries_line_no() -> None:
    bad_diff = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,2 +1,2 @@
-line1
"""
    with pytest.raises(DiffParseError) as exc_info:
        parse_unified_diff(bad_diff)
    assert exc_info.value.line_no > 0
    assert exc_info.value.message


def test_parse_b_prefix_stripped_from_path() -> None:
    diff = "--- a/some/path.py\n+++ b/some/path.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    result = parse_unified_diff(diff)
    assert result.target_path == "some/path.py"
