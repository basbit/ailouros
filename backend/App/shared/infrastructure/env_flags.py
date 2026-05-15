from __future__ import annotations

import os

__all__ = ["is_truthy_env"]


def is_truthy_env(variable_name: str, default: bool = False) -> bool:
    raw = os.environ.get(variable_name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
