"""Tests for desktop-aware behaviour of rest_misc_service.defaults_payload."""

from __future__ import annotations

import pytest

from backend.App.integrations.application.rest_misc_service import (
    _local_default_model_id,
    _MODEL_DEFAULTS,
    _resolve_model_defaults_for_response,
    defaults_payload,
)


def test_local_default_model_id_uses_swarm_model_env(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL", "alias-from-env")
    assert _local_default_model_id() == "alias-from-env"


def test_local_default_model_id_falls_back_to_alias(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    assert _local_default_model_id() == "local-default"


def test_model_defaults_table_has_local_for_every_role():
    for role, providers in _MODEL_DEFAULTS.items():
        assert "local" in providers, f"role {role!r} missing 'local' default"


def test_resolve_model_defaults_uses_alias(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL", "alias-x")
    resolved = _resolve_model_defaults_for_response()
    for role, providers in resolved.items():
        assert providers["local"] == "alias-x", f"role {role} did not pick alias"


def test_defaults_payload_desktop_mode_picks_local(monkeypatch):
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    monkeypatch.delenv("SWARM_DEFAULT_ENVIRONMENT", raising=False)
    payload = defaults_payload()
    assert payload["default_role_environment"] == "local"
    assert payload["default_swarm_provider"] == "local"
    assert payload["desktop"] is True


def test_defaults_payload_web_mode_keeps_ollama(monkeypatch):
    monkeypatch.delenv("AILOUROS_DESKTOP", raising=False)
    monkeypatch.delenv("SWARM_DEFAULT_ENVIRONMENT", raising=False)
    payload = defaults_payload()
    assert payload["default_role_environment"] == "ollama"
    assert payload["default_swarm_provider"] == "ollama"
    assert payload["desktop"] is False


def test_defaults_payload_explicit_swarm_default_environment_wins(monkeypatch):
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    monkeypatch.setenv("SWARM_DEFAULT_ENVIRONMENT", "lmstudio")
    payload = defaults_payload()
    assert payload["default_role_environment"] == "lmstudio"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("AILOUROS_DESKTOP", raising=False)
    monkeypatch.delenv("SWARM_DEFAULT_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    yield
