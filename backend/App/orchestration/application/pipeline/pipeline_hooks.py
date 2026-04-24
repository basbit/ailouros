
from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Mapping
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_MODULE_CACHE: dict[str, Any] = {}


def _hooks_module_path_for_state(state: Mapping[str, Any]) -> str:
    ac = state.get("agent_config")
    if isinstance(ac, dict):
        sw = ac.get("swarm")
        if isinstance(sw, dict):
            p = str(sw.get("pipeline_hooks_module") or "").strip()
            if p:
                return p
    return os.getenv("SWARM_PIPELINE_HOOKS_MODULE", "").strip()


def _load_hooks(state: Mapping[str, Any]) -> tuple[
    Optional[Callable[..., Any]],
    Optional[Callable[..., Any]],
]:
    path = _hooks_module_path_for_state(state)
    if not path:
        return None, None
    if path not in _MODULE_CACHE:
        _MODULE_CACHE[path] = importlib.import_module(path)
    mod = _MODULE_CACHE[path]
    before = getattr(mod, "before_pipeline_step", None)
    after = getattr(mod, "after_pipeline_step", None)
    if before is not None and not callable(before):
        before = None
    if after is not None and not callable(after):
        after = None
    return before, after


def run_pipeline_hooks_before(
    step_id: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    before, _ = _load_hooks(state)
    if not before:
        return {}
    try:
        raw = before(step_id, state)
    except Exception as exc:
        logger.warning("before_pipeline_step hook raised for step %r: %s", step_id, exc, exc_info=True)
        return {}
    if not raw:
        return {}
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def run_pipeline_hooks_after(
    step_id: str,
    state: Mapping[str, Any],
    step_delta: Mapping[str, Any],
) -> None:
    _, after = _load_hooks(state)
    if not after:
        return
    try:
        after(step_id, state, step_delta)
    except Exception as exc:
        logger.warning("after_pipeline_step hook raised for step %r: %s", step_id, exc, exc_info=True)


def clear_hooks_cache_for_tests() -> None:
    _MODULE_CACHE.clear()
