"""Пример хука: после ``human_qa`` дописать итог в pattern memory.

Подключение: ``SWARM_PIPELINE_HOOKS_MODULE=examples.swarm_hooks_store_patterns``
или ``agent_config.swarm.pipeline_hooks_module`` (приоритетнее).

Нужны ``swarm.pattern_memory`` / ``SWARM_PATTERN_MEMORY=1`` и путь к JSON
(см. ``backend.App.integrations.infrastructure.pattern_memory``).
"""

from __future__ import annotations

from typing import Any, Mapping


def before_pipeline_step(
    step_id: str,
    state: Mapping[str, Any],
) -> dict[str, Any] | None:
    return None


def after_pipeline_step(
    step_id: str,
    state: Mapping[str, Any],
    step_delta: Mapping[str, Any],
) -> None:
    if step_id != "human_qa":
        return
    # TODO: update imports — integrations/ top-level package was migrated to backend/App/integrations/
    from backend.App.integrations.infrastructure.pattern_memory import (  # type: ignore[import]
        pattern_memory_enabled,
        pattern_memory_path_for_state,
        store_pattern,
    )

    if not pattern_memory_enabled(state):
        return
    text_in = str(state.get("input") or "").strip()
    if not text_in:
        return
    key = text_in.split("\n")[0].strip()[:200]
    if len(key) < 3:
        return
    final = (
        str(state.get("qa_human_output") or "")
        or str(state.get("qa_output") or "")
    ).strip()
    if len(final) < 30:
        return
    path = pattern_memory_path_for_state(state)
    store_pattern(path, "default", key, final, merge=True)
