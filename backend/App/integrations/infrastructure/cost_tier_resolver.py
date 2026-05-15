from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from backend.App.integrations.domain.cost_tier import (
    CostTier,
    CostTierViolation,
    RoleTierPolicy,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHED_CONFIG: Optional[dict[str, Any]] = None

_VALID_TIERS: tuple[CostTier, ...] = ("cheap", "mid", "flagship")


def _parse_config(raw: dict[str, Any]) -> dict[str, Any]:
    tiers_raw = raw.get("tiers")
    if not isinstance(tiers_raw, dict):
        raise RuntimeError("cost_tiers.json: 'tiers' must be an object")

    for tier_name in _VALID_TIERS:
        models = tiers_raw.get(tier_name)
        if models is None:
            raise RuntimeError(
                f"cost_tiers.json: tier '{tier_name}' is missing entirely; "
                f"every tier must be present and non-empty."
            )
        if not isinstance(models, list) or len(models) == 0:
            raise RuntimeError(
                f"cost_tiers.json: tier '{tier_name}' must be a non-empty list of model strings"
            )

    all_models: list[str] = []
    for tier_name in _VALID_TIERS:
        all_models.extend(tiers_raw[tier_name])
    duplicates = {m for m in all_models if all_models.count(m) > 1}
    if duplicates:
        raise RuntimeError(
            f"cost_tiers.json: model(s) appear in more than one tier: {sorted(duplicates)!r}"
        )

    policies_raw = raw.get("role_policies")
    if not isinstance(policies_raw, dict):
        raise RuntimeError("cost_tiers.json: 'role_policies' must be an object")

    parsed_policies: dict[str, RoleTierPolicy] = {}
    for role, pol_raw in policies_raw.items():
        if not isinstance(pol_raw, dict):
            raise RuntimeError(
                f"cost_tiers.json: role_policies['{role}'] must be an object"
            )
        allowed_raw = pol_raw.get("allowed_tiers")
        preferred_raw = pol_raw.get("preferred_tier")
        if not isinstance(allowed_raw, list) or not allowed_raw:
            raise RuntimeError(
                f"cost_tiers.json: role_policies['{role}'].allowed_tiers must be a non-empty list"
            )
        for t in allowed_raw:
            if t not in _VALID_TIERS:
                raise RuntimeError(
                    f"cost_tiers.json: role_policies['{role}'].allowed_tiers contains unknown tier '{t}'; "
                    f"valid tiers: {list(_VALID_TIERS)!r}"
                )
        if preferred_raw not in _VALID_TIERS:
            raise RuntimeError(
                f"cost_tiers.json: role_policies['{role}'].preferred_tier='{preferred_raw}' "
                f"is not a valid tier; valid tiers: {list(_VALID_TIERS)!r}"
            )
        if preferred_raw not in allowed_raw:
            raise RuntimeError(
                f"cost_tiers.json: role_policies['{role}'].preferred_tier='{preferred_raw}' "
                f"is not in allowed_tiers={allowed_raw!r}"
            )
        parsed_policies[role] = RoleTierPolicy(
            role=role,
            allowed_tiers=tuple(allowed_raw),
            preferred_tier=preferred_raw,
        )

    model_to_tier: dict[str, CostTier] = {}
    for tier_name in _VALID_TIERS:
        for model in tiers_raw[tier_name]:
            model_to_tier[model] = tier_name

    return {
        "tiers": {t: list(tiers_raw[t]) for t in _VALID_TIERS},
        "role_policies": parsed_policies,
        "model_to_tier": model_to_tier,
    }


def _load_uncached() -> dict[str, Any]:
    raw = load_app_config_json("cost_tiers.json")
    parsed = _parse_config(raw)
    policy_count = len(parsed["role_policies"])
    model_count = len(parsed["model_to_tier"])
    logger.info(
        "cost_tiers: loaded %d role policies, %d mapped models",
        policy_count,
        model_count,
    )
    return parsed


def load_cost_tier_config() -> dict[str, Any]:
    global _CACHED_CONFIG
    if _CACHED_CONFIG is not None:
        return _CACHED_CONFIG
    with _CACHE_LOCK:
        if _CACHED_CONFIG is None:
            _CACHED_CONFIG = _load_uncached()
        return _CACHED_CONFIG


def reset_cost_tier_cache() -> None:
    global _CACHED_CONFIG
    with _CACHE_LOCK:
        _CACHED_CONFIG = None
    load_app_config_json.cache_clear()


def resolve_tier_for_model(model: str) -> CostTier:
    cfg = load_cost_tier_config()
    tier = cfg["model_to_tier"].get(model)
    if tier is None:
        raise RuntimeError(
            f"cost_tiers: model '{model}' is not registered in any tier. "
            f"Add it to cost_tiers.json under the appropriate tier."
        )
    return tier


def enforce_role_tier(role: str, model: str) -> None:
    cfg = load_cost_tier_config()
    policy: Optional[RoleTierPolicy] = cfg["role_policies"].get(role)
    if policy is None:
        return
    actual_tier = resolve_tier_for_model(model)
    if actual_tier not in policy.allowed_tiers:
        raise CostTierViolation(
            role=role,
            model=model,
            actual_tier=actual_tier,
            allowed_tiers=policy.allowed_tiers,
        )


def pick_default_model_for_role(role: str) -> str:
    cfg = load_cost_tier_config()
    policy: Optional[RoleTierPolicy] = cfg["role_policies"].get(role)
    if policy is None:
        raise RuntimeError(
            f"cost_tiers: no policy defined for role '{role}'; "
            f"cannot pick a default model."
        )
    preferred_tier = policy.preferred_tier
    candidates = cfg["tiers"].get(preferred_tier, [])
    if not candidates:
        raise RuntimeError(
            f"cost_tiers: preferred tier '{preferred_tier}' for role '{role}' has no models configured."
        )
    return candidates[0]


def get_cost_tier_config_for_response() -> dict[str, Any]:
    cfg = load_cost_tier_config()
    return {
        "tiers": cfg["tiers"],
        "role_policies": {
            role: {
                "allowed_tiers": list(pol.allowed_tiers),
                "preferred_tier": pol.preferred_tier,
            }
            for role, pol in cfg["role_policies"].items()
        },
    }


__all__ = [
    "load_cost_tier_config",
    "reset_cost_tier_cache",
    "resolve_tier_for_model",
    "enforce_role_tier",
    "pick_default_model_for_role",
    "get_cost_tier_config_for_response",
]
