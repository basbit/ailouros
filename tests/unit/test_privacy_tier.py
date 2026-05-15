from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.App.shared.domain.exceptions import PrivacyTierViolation


# ---------------------------------------------------------------------------
# Helpers — isolate the retention config from global cache
# ---------------------------------------------------------------------------

def _patch_retention(providers: dict[str, Any]):
    fake_config = {"providers": providers}

    def _fake_load() -> dict[str, Any]:
        return fake_config

    return patch(
        "backend.App.orchestration.infrastructure.agents.model_resolver._load_retention_config",
        side_effect=_fake_load,
    )


# ---------------------------------------------------------------------------
# enforce_privacy_tier — public tier: any provider allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["anthropic", "openai", "ollama", "lmstudio", "unknown"])
def test_public_tier_allows_any_provider(provider: str) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "anthropic": {"data_retention": "none", "local": False},
        "openai": {"data_retention": "none", "local": False},
        "ollama": {"data_retention": "local", "local": True},
        "lmstudio": {"data_retention": "local", "local": True},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value=provider,
        ):
            enforce_privacy_tier("dev", "some-model", "public")


# ---------------------------------------------------------------------------
# enforce_privacy_tier — internal tier: local or no-retention allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["ollama", "lmstudio"])
def test_internal_tier_allows_local_providers(provider: str) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "ollama": {"data_retention": "local", "local": True},
        "lmstudio": {"data_retention": "local", "local": True},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value=provider,
        ):
            enforce_privacy_tier("ba", "model", "internal")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_internal_tier_allows_no_retention_cloud_providers(provider: str) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "anthropic": {"data_retention": "none", "local": False},
        "openai": {"data_retention": "none", "local": False},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value=provider,
        ):
            enforce_privacy_tier("pm", "model", "internal")


def test_internal_tier_blocks_retaining_provider() -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "unknown_cloud": {"data_retention": "standard", "local": False},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value="unknown_cloud",
        ):
            with pytest.raises(PrivacyTierViolation) as exc_info:
                enforce_privacy_tier("arch", "model", "internal")
    assert exc_info.value.tier == "internal"
    assert exc_info.value.provider == "unknown_cloud"
    assert "internal" in exc_info.value.remediation.lower()


# ---------------------------------------------------------------------------
# enforce_privacy_tier — secret tier: local only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["ollama", "lmstudio"])
def test_secret_tier_allows_local_providers(provider: str) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "ollama": {"data_retention": "local", "local": True},
        "lmstudio": {"data_retention": "local", "local": True},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value=provider,
        ):
            enforce_privacy_tier("dev", "model", "secret")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_secret_tier_blocks_cloud_even_no_retention(provider: str) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import enforce_privacy_tier

    providers = {
        "anthropic": {"data_retention": "none", "local": False},
        "openai": {"data_retention": "none", "local": False},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
            return_value=provider,
        ):
            with pytest.raises(PrivacyTierViolation) as exc_info:
                enforce_privacy_tier("dev", "model", "secret")
    assert exc_info.value.tier == "secret"
    assert exc_info.value.provider == provider
    assert "secret" in exc_info.value.remediation.lower()


# ---------------------------------------------------------------------------
# PrivacyTierViolation fields
# ---------------------------------------------------------------------------

def test_privacy_tier_violation_fields() -> None:
    exc = PrivacyTierViolation(tier="secret", provider="openai", remediation="Use local.")
    assert exc.tier == "secret"
    assert exc.provider == "openai"
    assert exc.remediation == "Use local."
    assert "secret" in str(exc)
    assert "openai" in str(exc)


# ---------------------------------------------------------------------------
# providers_retention.json — config file structure
# ---------------------------------------------------------------------------

def test_providers_retention_config_has_required_providers(tmp_path: Path) -> None:
    config_path = (
        Path(__file__).resolve().parents[2] / "config" / "providers_retention.json"
    )
    assert config_path.is_file(), "providers_retention.json must exist"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    providers = data.get("providers", {})
    assert "anthropic" in providers
    assert "openai" in providers
    assert "ollama" in providers
    assert "lmstudio" in providers


def test_anthropic_and_openai_have_no_retention(tmp_path: Path) -> None:
    config_path = (
        Path(__file__).resolve().parents[2] / "config" / "providers_retention.json"
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    providers = data["providers"]
    assert providers["anthropic"]["data_retention"] == "none"
    assert providers["openai"]["data_retention"] == "none"
    assert providers["anthropic"]["local"] is False
    assert providers["openai"]["local"] is False


def test_ollama_and_lmstudio_are_local(tmp_path: Path) -> None:
    config_path = (
        Path(__file__).resolve().parents[2] / "config" / "providers_retention.json"
    )
    data = json.loads(config_path.read_text(encoding="utf-8"))
    providers = data["providers"]
    assert providers["ollama"]["local"] is True
    assert providers["lmstudio"]["local"] is True


# ---------------------------------------------------------------------------
# resolve_model_with_privacy — agent_config privacy override
# ---------------------------------------------------------------------------

def test_resolve_model_with_privacy_respects_agent_config_privacy(
    monkeypatch,
) -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    monkeypatch.setenv("SWARM_COST_TIER_DISABLED", "1")
    providers = {
        "openai": {"data_retention": "none", "local": False},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="gpt-4o",
        ):
            with patch(
                "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
                return_value="openai",
            ):
                model = resolve_model_with_privacy(
                    "dev",
                    "gpt-4o",
                    privacy="internal",
                    agent_config={"dev": {"privacy": "internal"}},
                )
    assert model == "gpt-4o"


def test_resolve_model_with_privacy_raises_on_secret_with_cloud() -> None:
    from backend.App.orchestration.infrastructure.agents.model_resolver import (
        resolve_model_with_privacy,
    )

    providers = {
        "anthropic": {"data_retention": "none", "local": False},
    }
    with _patch_retention(providers):
        with patch(
            "backend.App.orchestration.infrastructure.agents.model_resolver.resolve_model",
            return_value="claude-3-5-sonnet-latest",
        ):
            with patch(
                "backend.App.orchestration.infrastructure.agents.model_resolver._infer_provider_from_model",
                return_value="anthropic",
            ):
                with pytest.raises(PrivacyTierViolation):
                    resolve_model_with_privacy(
                        "dev",
                        "claude-3-5-sonnet-latest",
                        privacy="secret",
                    )
