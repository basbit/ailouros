from __future__ import annotations

import pytest

from backend.App.spec.domain.keep_regions import (
    KeepRegionError,
    apply_keep_regions,
    extract_keep_regions,
)

_SIMPLE = """\
def foo():
    pass
# @spec-keep begin custom-logic
def bar():
    return 42
# @spec-keep end
def baz():
    pass
"""

_MULTI = """\
# @spec-keep begin section-a
x = 1
# @spec-keep end
y = 2
# @spec-keep begin section-b
z = 3
# @spec-keep end
"""


def test_extract_single_region():
    regions = extract_keep_regions(_SIMPLE)
    assert len(regions) == 1
    assert regions[0].reason == "custom-logic"
    assert "def bar():" in regions[0].content


def test_extract_multi_regions():
    regions = extract_keep_regions(_MULTI)
    assert len(regions) == 2
    assert {r.reason for r in regions} == {"section-a", "section-b"}


def test_extract_empty_text_returns_empty():
    assert extract_keep_regions("") == ()


def test_extract_no_markers_returns_empty():
    assert extract_keep_regions("def foo(): pass\n") == ()


def test_nested_begin_raises():
    bad = "# @spec-keep begin a\n# @spec-keep begin b\n# @spec-keep end\n"
    with pytest.raises(KeepRegionError, match="nested"):
        extract_keep_regions(bad)


def test_orphan_end_raises():
    bad = "x = 1\n# @spec-keep end\n"
    with pytest.raises(KeepRegionError, match="no matching begin"):
        extract_keep_regions(bad)


def test_unclosed_begin_raises():
    bad = "# @spec-keep begin open\nx = 1\n"
    with pytest.raises(KeepRegionError, match="never closed"):
        extract_keep_regions(bad)


def test_apply_preserves_matching_region():
    new_text = "def foo():\n    pass\n# @spec-keep begin custom-logic\ndef bar():\n    return 0\n# @spec-keep end\n"
    result = apply_keep_regions(_SIMPLE, new_text)
    assert "return 42" in result
    assert "return 0" not in result


def test_apply_appends_region_not_in_new_text():
    new_text = "def foo():\n    pass\n"
    result = apply_keep_regions(_SIMPLE, new_text)
    assert "return 42" in result


def test_apply_preserves_multiple_regions():
    new_text = "# @spec-keep begin section-a\nx = 99\n# @spec-keep end\n# @spec-keep begin section-b\nz = 99\n# @spec-keep end\n"
    result = apply_keep_regions(_MULTI, new_text)
    assert "x = 1" in result
    assert "z = 3" in result
    assert "x = 99" not in result
    assert "z = 99" not in result


def test_apply_empty_existing_returns_new():
    new_text = "# generated\n"
    assert apply_keep_regions("", new_text) == new_text


def test_apply_no_regions_in_existing_returns_new():
    result = apply_keep_regions("old code\n", "new code\n")
    assert result == "new code\n"
