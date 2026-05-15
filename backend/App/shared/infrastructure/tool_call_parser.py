
from __future__ import annotations

import json
from typing import Any

__all__ = ["parse_tool_call_args"]


def parse_tool_call_args(args_str: str | None) -> dict[str, Any]:
    raw = (args_str or "").strip()
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    for key, value in list(parsed.items()):
        if (
            isinstance(value, str)
            and value
            and value[0] in ("[", "{")
        ):
            try:
                nested = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(nested, (list, dict)):
                parsed[key] = nested
    return parsed
