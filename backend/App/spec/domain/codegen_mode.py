from __future__ import annotations

from typing import Literal

CodegenMode = Literal["full_file", "diff", "sketch_fill"]

_DIFF_ELIGIBLE_STATUSES = {"reviewed", "stable"}


def choose_mode(
    target_path_exists: bool,
    spec_status: str,
    *,
    force_full: bool,
    force_diff: bool,
) -> CodegenMode:
    if force_full:
        return "full_file"
    if force_diff:
        return "diff"
    if target_path_exists and spec_status in _DIFF_ELIGIBLE_STATUSES:
        return "diff"
    return "full_file"


__all__ = ["CodegenMode", "choose_mode"]
