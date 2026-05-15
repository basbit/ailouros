from __future__ import annotations

import logging
import threading
from typing import Optional

from backend.App.integrations.domain.role_budgets import (
    RoleBudget,
    parse_role_budgets,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHED: Optional[dict[str, RoleBudget]] = None


def _load_uncached() -> dict[str, RoleBudget]:
    raw = load_app_config_json("role_budgets.json")
    parsed = parse_role_budgets(raw)
    logger.info("role_budgets: loaded %d role budget(s)", len(parsed))
    return parsed


def load_role_budgets() -> dict[str, RoleBudget]:
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    with _CACHE_LOCK:
        if _CACHED is None:
            _CACHED = _load_uncached()
        return _CACHED


def reset_role_budgets_cache() -> None:
    global _CACHED
    with _CACHE_LOCK:
        _CACHED = None
    load_app_config_json.cache_clear()


def get_role_budget(role: str) -> Optional[RoleBudget]:
    budgets = load_role_budgets()
    return budgets.get(role)


__all__ = [
    "load_role_budgets",
    "reset_role_budgets_cache",
    "get_role_budget",
]
