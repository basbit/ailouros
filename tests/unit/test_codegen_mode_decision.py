from __future__ import annotations

import pytest

from backend.App.spec.domain.codegen_mode import choose_mode


@pytest.mark.parametrize(
    "exists,status,force_full,force_diff,expected",
    [
        (True, "reviewed", False, False, "diff"),
        (True, "stable", False, False, "diff"),
        (True, "draft", False, False, "full_file"),
        (True, "deprecated", False, False, "full_file"),
        (False, "reviewed", False, False, "full_file"),
        (False, "stable", False, False, "full_file"),
        (False, "draft", False, False, "full_file"),
        (True, "reviewed", True, False, "full_file"),
        (True, "stable", True, False, "full_file"),
        (True, "reviewed", False, True, "diff"),
        (False, "draft", False, True, "diff"),
        (True, "reviewed", True, True, "full_file"),
        (True, "stable", True, True, "full_file"),
    ],
)
def test_choose_mode(
    exists: bool,
    status: str,
    force_full: bool,
    force_diff: bool,
    expected: str,
) -> None:
    result = choose_mode(
        target_path_exists=exists,
        spec_status=status,
        force_full=force_full,
        force_diff=force_diff,
    )
    assert result == expected
