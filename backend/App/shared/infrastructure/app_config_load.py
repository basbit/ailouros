from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from config.runtime import resolve_app_config_path

__all__ = ["load_app_config_json"]


@lru_cache(maxsize=48)
def load_app_config_json(relative_path: str, *, env_var: str = "") -> dict[str, Any]:
    path = resolve_app_config_path(relative_path, env_var=env_var)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise TypeError(
            f"{relative_path}: root JSON value must be an object, got {type(data)}"
        )
    return data
