"""Redact secret-looking values out of config/state dicts before persistence.

Cross-cutting — any domain that serialises state (sessions, pipeline
snapshots, memory, traces, …) should funnel through the same redactor so the
rules live in one place.

Only keys named ``api_key`` or ending in ``_api_key`` are redacted today;
extend this module if new secret-bearing keys appear.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

__all__ = ["redact_agent_config_secrets"]


_REDACTED = "***REDACTED***"


def _is_api_key_name(key: str) -> bool:
    return key == "api_key" or key.endswith("_api_key")


def redact_agent_config_secrets(
    agent_config: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Deep-copy ``agent_config`` and replace any string ``*api_key`` values with ``***REDACTED***``.

    Returns an empty ``{}`` for ``None`` / empty input so callers can chain.
    """
    if not agent_config:
        return {}

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, inner in value.items():
                key_s = str(key)
                if (
                    isinstance(inner, str)
                    and inner
                    and _is_api_key_name(key_s)
                ):
                    out[key_s] = _REDACTED
                else:
                    out[key_s] = _walk(inner)
            return out
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(copy.deepcopy(agent_config))
