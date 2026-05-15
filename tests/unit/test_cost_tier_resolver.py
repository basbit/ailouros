from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from backend.App.integrations.domain.cost_tier import CostTierViolation


_GOOD_CONFIG: dict[str, Any] = {
    "tiers": {
        "cheap": ["model-cheap-a", "model-cheap-b"],
        "mid": ["model-mid-a"],
        "flagship": ["model-flagship-a"],
    },
    "role_policies": {
        "verifier": {"allowed_tiers": ["cheap"], "preferred_tier": "cheap"},
        "coder": {"allowed_tiers": ["mid", "flagship"], "preferred_tier": "mid"},
        "reviewer": {"allowed_tiers": ["flagship"], "preferred_tier": "flagship"},
    },
}


def _patch_loader(cfg: dict[str, Any]):
    return patch(
        "backend.App.integrations.infrastructure.cost_tier_resolver.load_app_config_json",
        return_value=cfg,
    )


def _reset():
    import backend.App.integrations.infrastructure.cost_tier_resolver as mod
    mod._CACHED_CONFIG = None


def test_load_happy_path() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        cfg = load_cost_tier_config()
    assert "model-cheap-a" in cfg["model_to_tier"]
    assert cfg["model_to_tier"]["model-cheap-a"] == "cheap"
    assert cfg["model_to_tier"]["model-flagship-a"] == "flagship"


def test_load_is_memoised() -> None:
    _reset()
    call_count = 0

    def _fake_loader(name: str, **_kw: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return _GOOD_CONFIG

    with patch(
        "backend.App.integrations.infrastructure.cost_tier_resolver.load_app_config_json",
        side_effect=_fake_loader,
    ):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        load_cost_tier_config()
        load_cost_tier_config()

    assert call_count == 1


def test_load_fails_on_empty_tier() -> None:
    _reset()
    bad = {
        "tiers": {"cheap": [], "mid": ["m"], "flagship": ["f"]},
        "role_policies": {},
    }
    with _patch_loader(bad):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        with pytest.raises(RuntimeError, match="non-empty"):
            load_cost_tier_config()


def test_load_fails_on_missing_tier() -> None:
    _reset()
    bad = {
        "tiers": {"cheap": ["m"], "mid": ["n"]},
        "role_policies": {},
    }
    with _patch_loader(bad):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        with pytest.raises(RuntimeError, match="flagship"):
            load_cost_tier_config()


def test_load_fails_on_duplicate_model() -> None:
    _reset()
    bad = {
        "tiers": {"cheap": ["dup"], "mid": ["dup"], "flagship": ["f"]},
        "role_policies": {},
    }
    with _patch_loader(bad):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        with pytest.raises(RuntimeError, match="more than one tier"):
            load_cost_tier_config()


def test_load_fails_preferred_not_in_allowed() -> None:
    _reset()
    bad = {
        "tiers": {"cheap": ["c"], "mid": ["m"], "flagship": ["f"]},
        "role_policies": {
            "bad_role": {"allowed_tiers": ["cheap"], "preferred_tier": "mid"},
        },
    }
    with _patch_loader(bad):
        from backend.App.integrations.infrastructure.cost_tier_resolver import load_cost_tier_config
        with pytest.raises(RuntimeError, match="preferred_tier"):
            load_cost_tier_config()


def test_resolve_tier_for_model_known() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import resolve_tier_for_model
        assert resolve_tier_for_model("model-mid-a") == "mid"


def test_resolve_tier_for_model_unknown_raises() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import resolve_tier_for_model
        with pytest.raises(RuntimeError, match="not registered"):
            resolve_tier_for_model("no-such-model")


def test_enforce_role_tier_allowed() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import enforce_role_tier
        enforce_role_tier("verifier", "model-cheap-a")


def test_enforce_role_tier_violation_raises() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import enforce_role_tier
        with pytest.raises(CostTierViolation) as exc_info:
            enforce_role_tier("verifier", "model-flagship-a")
    assert exc_info.value.role == "verifier"
    assert exc_info.value.model == "model-flagship-a"


def test_enforce_role_tier_unknown_role_is_noop() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import enforce_role_tier
        enforce_role_tier("unknown_role", "model-flagship-a")


def test_pick_default_model_for_role() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import pick_default_model_for_role
        model = pick_default_model_for_role("verifier")
    assert model == "model-cheap-a"


def test_pick_default_model_for_unknown_role_raises() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import pick_default_model_for_role
        with pytest.raises(RuntimeError, match="no policy"):
            pick_default_model_for_role("ghost_role")


def test_get_config_for_response_shape() -> None:
    _reset()
    with _patch_loader(_GOOD_CONFIG):
        from backend.App.integrations.infrastructure.cost_tier_resolver import get_cost_tier_config_for_response
        result = get_cost_tier_config_for_response()
    assert "tiers" in result
    assert "role_policies" in result
    assert "verifier" in result["role_policies"]
    pol = result["role_policies"]["verifier"]
    assert "allowed_tiers" in pol
    assert "preferred_tier" in pol
