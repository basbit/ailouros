"""Tests for orchestrator.model_discovery."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.model_discovery import (
    DiscoveredModel,
    ModelAssignment,
    _model_cache,
    _model_backoff_until,
    assign_models_to_roles,
    discover_cloud_models,
    discover_ollama_models,
    load_models_config,
    save_models_config,
)


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """Clear the TTL model cache before each test to avoid cross-test pollution."""
    _model_cache.clear()
    _model_backoff_until.clear()
    yield
    _model_cache.clear()
    _model_backoff_until.clear()


class TestDiscoverOllama:
    def test_discover_ollama_unavailable(self):
        """When Ollama is not reachable, returns empty list without raising."""
        import httpx

        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = discover_ollama_models()
        assert result == []

    def test_discover_ollama_returns_models(self):
        """Successful response is parsed into DiscoveredModel list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [{"name": "llama3:8b"}, {"name": "qwen2.5:14b"}]
        }
        with patch("httpx.get", return_value=mock_resp):
            result = discover_ollama_models()
        assert len(result) == 2
        assert result[0].model_id == "llama3:8b"
        assert result[0].provider == "ollama"

    def test_discover_ollama_uses_cached_result_during_backoff(self):
        """Failed refresh returns the last cached result instead of thrashing /v1/models."""
        success = MagicMock()
        success.status_code = 200
        success.json.return_value = {"models": [{"name": "llama3:8b"}]}

        with patch("httpx.get", return_value=success):
            first = discover_ollama_models()
        assert len(first) == 1

        with patch("httpx.get", side_effect=RuntimeError("down")):
            second = discover_ollama_models()
        assert len(second) == 1
        assert second[0].model_id == "llama3:8b"


class TestDiscoverCloud:
    def test_discover_cloud_no_keys(self, monkeypatch):
        """Without API keys, returns empty list."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = discover_cloud_models()
        assert result == []

    def test_discover_cloud_with_anthropic_key(self, monkeypatch):
        """With ANTHROPIC_API_KEY set, returns 3 Claude models."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = discover_cloud_models()
        assert len(result) == 3
        providers = {m.provider for m in result}
        assert providers == {"anthropic"}

    def test_discover_cloud_with_openai_key(self, monkeypatch):
        """With OPENAI_API_KEY set, returns 3 OpenAI models."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        result = discover_cloud_models()
        assert len(result) == 3
        assert all(m.provider == "openai" for m in result)


class TestAssignModels:
    def test_assign_models_empty_list(self):
        """No models discovered → returns empty list (no crash)."""
        result = assign_models_to_roles([])
        assert result == []

    def test_assign_models_local_preferred(self):
        """Local models are preferred over cloud models."""
        models = [
            DiscoveredModel(model_id="qwen2.5:14b", provider="ollama"),
            DiscoveredModel(model_id="claude-opus-4-5", provider="anthropic"),
        ]
        assignments = assign_models_to_roles(models)
        # All assigned roles should prefer the local model
        for a in assignments:
            assert a.provider == "ollama", (
                f"role={a.role} expected ollama but got {a.provider}"
            )

    def test_assign_models_cloud_fallback(self, monkeypatch):
        """When no local models, falls back to cloud."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        models = [
            DiscoveredModel(model_id="claude-opus-4-5", provider="anthropic"),
            DiscoveredModel(model_id="claude-sonnet-4-5", provider="anthropic"),
            DiscoveredModel(model_id="claude-haiku-4-5", provider="anthropic"),
        ]
        assignments = assign_models_to_roles(models)
        assert len(assignments) > 0
        for a in assignments:
            assert a.provider == "anthropic"

    def test_assign_models_keyword_matching(self):
        """Dev role prefers coder-tagged models."""
        models = [
            DiscoveredModel(model_id="deepseek-coder-v2", provider="ollama"),
            DiscoveredModel(model_id="llama3:8b", provider="ollama"),
        ]
        assignments = assign_models_to_roles(models)
        dev = next((a for a in assignments if a.role == "dev"), None)
        assert dev is not None
        assert dev.model_id == "deepseek-coder-v2"


class TestSaveLoadModelsConfig:
    def test_save_and_load_round_trip(self, tmp_path):
        """Save then load models config round-trips correctly."""
        assignments = [
            ModelAssignment(
                role="pm", model_id="qwen2.5:14b", provider="ollama", reason="test"
            ),
            ModelAssignment(
                role="dev", model_id="deepseek-coder", provider="lm_studio", reason="test"
            ),
        ]
        save_models_config(str(tmp_path), assignments)

        config_path = tmp_path / ".swarm" / "models_config.json"
        assert config_path.exists()

        loaded = load_models_config(str(tmp_path))
        assert loaded is not None
        assert loaded["version"] == "1"
        assert loaded["roles"]["pm"]["model_id"] == "qwen2.5:14b"
        assert loaded["roles"]["dev"]["provider"] == "lm_studio"

    def test_load_nonexistent_returns_none(self, tmp_path):
        """Loading from a workspace with no config returns None."""
        result = load_models_config(str(tmp_path))
        assert result is None
