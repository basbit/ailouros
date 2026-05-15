from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Optional

from config.runtime import resolve_app_config_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextBudget:
    wiki_chars: int
    pattern_memory_chars: int
    cross_task_memory_chars: int
    knowledge_chars: int
    summaries_chars: int
    include_summaries: bool
    code_analysis_chars: int
    code_analysis_max_files: int
    fix_cycle_summary_chars: int
    state_max_chars: int
    reasoning_budget_tokens: int


DEFAULT_BUDGET: ContextBudget = ContextBudget(
    wiki_chars=6000,
    pattern_memory_chars=6000,
    cross_task_memory_chars=8000,
    knowledge_chars=2500,
    summaries_chars=300,
    include_summaries=True,
    code_analysis_chars=12_000,
    code_analysis_max_files=120,
    fix_cycle_summary_chars=4_000,
    state_max_chars=200_000,
    reasoning_budget_tokens=4096,
)

_FIELD_SET: frozenset[str] = frozenset(f.name for f in fields(ContextBudget))

_LEGACY_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "include_summaries": ("SWARM_CONTEXT_SUMMARIES",),
    "state_max_chars": ("SWARM_STATE_MAX_CHARS",),
}

_PROFILES_FILENAME = "context_budget_profiles.json"


def _coerce(field_name: str, raw: Any) -> Any:
    if field_name == "include_summaries":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid context-budget value for {field_name}={raw!r}: expected int. {exc}"
        ) from exc


def _apply_overrides(budget: ContextBudget, overrides: Any) -> ContextBudget:
    if not isinstance(overrides, dict):
        return budget
    updates: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in _FIELD_SET and value is not None:
            updates[key] = _coerce(key, value)
    return replace(budget, **updates) if updates else budget


def _profiles_path():
    return resolve_app_config_path(
        _PROFILES_FILENAME, env_var="SWARM_CONTEXT_BUDGET_PROFILES"
    )


def _load_profiles() -> tuple[dict[str, ContextBudget], dict[str, ContextBudget]]:
    import json

    path = _profiles_path()
    if not path.is_file():
        logger.debug("context_budget: no profiles file at %s — using DEFAULT_BUDGET only", path)
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "context_budget: failed to load %s (%s); using DEFAULT_BUDGET only",
            path, exc,
        )
        return {}, {}
    if not isinstance(raw, dict):
        logger.warning(
            "context_budget: %s is not a JSON object; using DEFAULT_BUDGET only", path,
        )
        return {}, {}

    templates_any = raw.get("profile_templates")
    templates: dict[str, Any] = templates_any if isinstance(templates_any, dict) else {}

    step_profiles: dict[str, ContextBudget] = {}
    tier_profiles: dict[str, ContextBudget] = {}
    for key, fields_dict in raw.items():
        if not isinstance(key, str) or key.startswith("_") or key == "profile_templates":
            continue
        if isinstance(fields_dict, str) and fields_dict.startswith("@"):
            tmpl = templates.get(fields_dict[1:])
            if not isinstance(tmpl, dict):
                logger.warning(
                    "context_budget: profile %r references missing template %r; skipped",
                    key,
                    fields_dict[1:],
                )
                continue
            fields_dict = tmpl
        if not isinstance(fields_dict, dict):
            logger.warning("context_budget: profile %r is not a dict; skipped", key)
            continue
        try:
            profile = _apply_overrides(DEFAULT_BUDGET, fields_dict)
        except ValueError as exc:
            logger.warning("context_budget: profile %r has invalid value (%s); skipped", key, exc)
            continue
        if key.endswith("_"):
            tier_profiles[key] = profile
        else:
            step_profiles[key] = profile
    logger.info(
        "context_budget: loaded %d step profile(s) + %d tier profile(s) from %s",
        len(step_profiles), len(tier_profiles), path,
    )
    return step_profiles, tier_profiles


_STEP_PROFILES, _TIER_PROFILES = _load_profiles()


def reload_profiles() -> None:
    global _STEP_PROFILES, _TIER_PROFILES
    _STEP_PROFILES, _TIER_PROFILES = _load_profiles()


def _profile_for(step_id: str) -> ContextBudget:
    profile = _STEP_PROFILES.get(step_id)
    if profile is not None:
        return profile
    for prefix, tier in _TIER_PROFILES.items():
        if step_id.startswith(prefix):
            return tier
    return DEFAULT_BUDGET


def _apply_env_overrides(budget: ContextBudget, step_id: str) -> ContextBudget:
    updates: dict[str, Any] = {}
    step_key = step_id.upper()
    for field_name in _FIELD_SET:
        env_base = f"SWARM_CONTEXT_{field_name.upper()}"
        candidates: list[str] = [f"{env_base}_{step_key}", env_base]
        for legacy_base in _LEGACY_ENV_ALIASES.get(field_name, ()):
            candidates.extend([f"{legacy_base}_{step_key}", legacy_base])
        for candidate in candidates:
            raw = os.environ.get(candidate, "").strip()
            if raw:
                try:
                    updates[field_name] = _coerce(field_name, raw)
                except ValueError as exc:
                    raise ValueError(f"{candidate}: {exc}") from exc
                break
    return replace(budget, **updates) if updates else budget


def get_context_budget(
    step_id: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> ContextBudget:
    budget = _profile_for(step_id)
    if isinstance(agent_config, dict):
        swarm_cfg = agent_config.get("swarm")
        if isinstance(swarm_cfg, dict):
            budgets_cfg = swarm_cfg.get("context_budgets")
            if isinstance(budgets_cfg, dict):
                budget = _apply_overrides(budget, budgets_cfg.get("default"))
                budget = _apply_overrides(budget, budgets_cfg.get(step_id))
    return _apply_env_overrides(budget, step_id)


def context_budget_as_dict(budget: ContextBudget) -> dict[str, Any]:
    return asdict(budget)


__all__ = [
    "ContextBudget",
    "DEFAULT_BUDGET",
    "context_budget_as_dict",
    "get_context_budget",
    "reload_profiles",
]
