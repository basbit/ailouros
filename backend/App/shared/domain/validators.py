from __future__ import annotations

import os
from pathlib import Path
from typing import Any

__all__ = [
    "assert_safe_path",
    "is_truthy_env",
    "is_truthy_value",
    "is_under",
]


def is_under(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_safe_path(path: Path, root: Path | str) -> None:
    resolved = path.resolve()
    root_resolved = Path(root).resolve()
    if not is_under(root_resolved, resolved):
        raise ValueError(
            f"Path {path!r} resolves to {resolved!r} which is outside "
            f"{root_resolved!r} — possible path traversal attempt"
        )


def is_truthy_env(variable_name: str, default: bool = False) -> bool:
    raw = os.environ.get(variable_name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_truthy_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")
