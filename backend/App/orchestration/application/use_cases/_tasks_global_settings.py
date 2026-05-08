from __future__ import annotations

from typing import Any


def apply_global_automation_settings(agent_config: dict[str, Any]) -> None:
    from backend.App.integrations.application.user_settings_service import (
        get_persisted_automation_settings,
    )

    persisted = get_persisted_automation_settings()
    swarm = agent_config.get("swarm")
    if not isinstance(swarm, dict):
        swarm = {}
        agent_config["swarm"] = swarm

    _apply_bool_keys(swarm, persisted, _BOOL_KEYS)
    _apply_str_keys(swarm, persisted, _STR_KEYS)
    _apply_int_keys(swarm, persisted, _INT_KEYS)
    _apply_planner(agent_config, persisted)


_BOOL_KEYS: tuple[tuple[str, str], ...] = (
    ("self_verify", "swarm_self_verify"),
    ("auto_retry", "swarm_auto_retry"),
    ("deep_planning", "swarm_deep_planning"),
    ("background_agent", "swarm_background_agent"),
    ("dream_enabled", "swarm_dream_enabled"),
    ("quality_gate_enabled", "swarm_quality_gate"),
    ("auto_plan", "swarm_auto_plan"),
)

_STR_KEYS: tuple[tuple[str, str], ...] = (
    ("self_verify_model", "swarm_self_verify_model"),
    ("self_verify_provider", "swarm_self_verify_provider"),
    ("auto_approve", "swarm_auto_approve"),
    ("deep_planning_model", "swarm_deep_planning_model"),
    ("deep_planning_provider", "swarm_deep_planning_provider"),
    ("background_agent_model", "swarm_background_agent_model"),
    ("background_agent_provider", "swarm_background_agent_provider"),
    ("background_watch_paths", "swarm_background_watch_paths"),
)

_INT_KEYS: tuple[tuple[str, str], ...] = (
    ("auto_approve_timeout", "swarm_auto_approve_timeout"),
    ("max_step_retries", "swarm_max_step_retries"),
)


def _apply_bool_keys(
    swarm: dict[str, Any],
    persisted: dict[str, Any],
    pairs: tuple[tuple[str, str], ...],
) -> None:
    for swarm_key, form_key in pairs:
        if persisted.get(form_key):
            swarm[swarm_key] = True
        else:
            swarm.pop(swarm_key, None)


def _apply_str_keys(
    swarm: dict[str, Any],
    persisted: dict[str, Any],
    pairs: tuple[tuple[str, str], ...],
) -> None:
    for swarm_key, form_key in pairs:
        value = str(persisted.get(form_key, "") or "").strip()
        if value:
            swarm[swarm_key] = value
        else:
            swarm.pop(swarm_key, None)


def _apply_int_keys(
    swarm: dict[str, Any],
    persisted: dict[str, Any],
    pairs: tuple[tuple[str, str], ...],
) -> None:
    for swarm_key, form_key in pairs:
        raw = str(persisted.get(form_key, "") or "").strip()
        if not raw:
            swarm.pop(swarm_key, None)
            continue
        try:
            parsed = int(raw)
        except ValueError:
            swarm.pop(swarm_key, None)
            continue
        if parsed > 0:
            swarm[swarm_key] = parsed
        else:
            swarm.pop(swarm_key, None)


def _apply_planner(
    agent_config: dict[str, Any],
    persisted: dict[str, Any],
) -> None:
    planner_model = str(persisted.get("swarm_planner_model", "") or "").strip()
    planner_provider = str(persisted.get("swarm_planner_provider", "") or "").strip()
    if planner_model:
        planner_cfg: dict[str, Any] = {"model": planner_model}
        if planner_provider:
            planner_cfg["environment"] = planner_provider
        agent_config["swarm_planner"] = planner_cfg
    else:
        agent_config.pop("swarm_planner", None)


__all__ = ("apply_global_automation_settings",)
