from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.integrations.infrastructure.model_discovery import DiscoveredModel
from backend.App.shared.domain.exceptions import PrivacyTierViolation


_PROVIDERS_RETENTION = {
    "providers": {
        "ollama": {"data_retention": "local", "local": True},
        "lm_studio": {"data_retention": "local", "local": True},
        "anthropic": {"data_retention": "none", "local": False},
    }
}


@pytest.fixture(autouse=True)
def _disable_cost_tier(monkeypatch):
    monkeypatch.setenv("SWARM_COST_TIER_DISABLED", "1")


def _patch_retention():
    return patch(
        "backend.App.orchestration.infrastructure.agents.model_resolver._load_retention_config",
        return_value=_PROVIDERS_RETENTION,
    )


def test_prefer_local_picks_lm_studio_when_available(monkeypatch):
    monkeypatch.setenv("SWARM_PREFER_LOCAL", "1")
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    with (
        _patch_retention(),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="claude-3-5-sonnet-latest",
        ),
        patch(
            "backend.App.integrations.infrastructure.model_discovery.pick_best_local_model",
            return_value=DiscoveredModel(model_id="qwen2.5-coder-32b", provider="lm_studio"),
        ),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value="lm_studio",
        ),
    ):
        assert resolve_model_with_privacy("dev", "claude-3-5-sonnet-latest", privacy="public") == "qwen2.5-coder-32b"


def test_prefer_local_secret_with_no_local_raises(monkeypatch):
    monkeypatch.setenv("SWARM_PREFER_LOCAL", "1")
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    with (
        _patch_retention(),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="claude-3-5-sonnet-latest",
        ),
        patch(
            "backend.App.integrations.infrastructure.model_discovery.pick_best_local_model",
            return_value=None,
        ),
    ):
        with pytest.raises(PrivacyTierViolation):
            resolve_model_with_privacy("dev", "claude-3-5-sonnet-latest", privacy="secret")


def test_prefer_local_public_with_no_local_keeps_configured(monkeypatch):
    monkeypatch.setenv("SWARM_PREFER_LOCAL", "1")
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    with (
        _patch_retention(),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="claude-3-5-sonnet-latest",
        ),
        patch(
            "backend.App.integrations.infrastructure.model_discovery.pick_best_local_model",
            return_value=None,
        ),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value="anthropic",
        ),
    ):
        assert resolve_model_with_privacy("dev", "claude-3-5-sonnet-latest", privacy="public") == "claude-3-5-sonnet-latest"


def test_prefer_local_off_unchanged(monkeypatch):
    monkeypatch.delenv("SWARM_PREFER_LOCAL", raising=False)
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    with (
        _patch_retention(),
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="claude-3-5-sonnet-latest",
        ),
        patch(
            "backend.App.integrations.infrastructure.model_discovery.pick_best_local_model",
        ) as picker,
        patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value="anthropic",
        ),
    ):
        result = resolve_model_with_privacy("dev", "claude-3-5-sonnet-latest", privacy="public")
        assert result == "claude-3-5-sonnet-latest"
        picker.assert_not_called()
