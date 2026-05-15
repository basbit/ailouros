from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.App.integrations.domain.cost_tier import CostTierViolation


_TIER_CONFIG: dict[str, Any] = {
    "tiers": {
        "cheap": ["model-cheap"],
        "mid": ["model-mid"],
        "flagship": ["model-flagship"],
    },
    "role_policies": {
        "code_verifier": {"allowed_tiers": ["cheap"], "preferred_tier": "cheap"},
        "architect": {"allowed_tiers": ["flagship"], "preferred_tier": "flagship"},
        "dev": {"allowed_tiers": ["mid", "flagship"], "preferred_tier": "mid"},
    },
}


def _patch_tier_loader(cfg: dict[str, Any]):
    return patch(
        "backend.App.integrations.infrastructure.cost_tier_resolver.load_app_config_json",
        return_value=cfg,
    )


def _reset_tier_cache():
    import backend.App.integrations.infrastructure.cost_tier_resolver as mod
    mod._CACHED_CONFIG = None


def _patch_resolve_model(returned: str):
    return patch(
        "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
        return_value=returned,
    )


def _patch_privacy_enforce():
    return patch(
        "backend.App.orchestration.infrastructure.agents.model_resolver.enforce_privacy_tier",
    )


def test_allowed_tier_model_passes() -> None:
    _reset_tier_cache()
    with (
        _patch_tier_loader(_TIER_CONFIG),
        _patch_resolve_model("model-mid"),
        _patch_privacy_enforce(),
        patch.dict("os.environ", {"SWARM_COST_TIER_DISABLED": "0"}),
    ):
        from backend.App.orchestration.infrastructure.agents.model_resolver import resolve_model_with_privacy
        result = resolve_model_with_privacy("dev", "model-mid")
    assert result == "model-mid"


def test_tier_mismatch_raises_violation() -> None:
    _reset_tier_cache()
    with (
        _patch_tier_loader(_TIER_CONFIG),
        _patch_resolve_model("model-flagship"),
        _patch_privacy_enforce(),
        patch.dict("os.environ", {"SWARM_COST_TIER_DISABLED": "0"}),
    ):
        from backend.App.orchestration.infrastructure.agents.model_resolver import resolve_model_with_privacy
        with pytest.raises(CostTierViolation) as exc_info:
            resolve_model_with_privacy("code_verifier", "some-other-default-model")
    assert "code_verifier" in str(exc_info.value)
    assert "flagship" in str(exc_info.value)


def test_preferred_tier_picked_when_role_model_equals_default() -> None:
    _reset_tier_cache()
    with (
        _patch_tier_loader(_TIER_CONFIG),
        _patch_resolve_model("model-cheap"),
        _patch_privacy_enforce(),
        patch.dict("os.environ", {"SWARM_COST_TIER_DISABLED": "0"}),
    ):
        from backend.App.orchestration.infrastructure.agents.model_resolver import resolve_model_with_privacy
        result = resolve_model_with_privacy("architect", "model-cheap")
    assert result == "model-flagship"


def test_kill_switch_disables_tier_enforcement() -> None:
    _reset_tier_cache()
    with (
        _patch_tier_loader(_TIER_CONFIG),
        _patch_resolve_model("model-flagship"),
        _patch_privacy_enforce(),
        patch.dict("os.environ", {"SWARM_COST_TIER_DISABLED": "1"}),
    ):
        from backend.App.orchestration.infrastructure.agents.model_resolver import resolve_model_with_privacy
        result = resolve_model_with_privacy("code_verifier", "model-flagship")
    assert result == "model-flagship"


def test_unknown_role_skips_enforcement() -> None:
    _reset_tier_cache()
    with (
        _patch_tier_loader(_TIER_CONFIG),
        _patch_resolve_model("model-flagship"),
        _patch_privacy_enforce(),
        patch.dict("os.environ", {"SWARM_COST_TIER_DISABLED": "0"}),
    ):
        from backend.App.orchestration.infrastructure.agents.model_resolver import resolve_model_with_privacy
        result = resolve_model_with_privacy("mystery_role", "model-flagship")
    assert result == "model-flagship"
