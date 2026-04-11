"""Tests for backend/App/integrations/infrastructure/llm/providers.py."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.llm.providers import (
    _ask_anthropic,
    _build_anthropic_client,
    _is_cloud_model,
    _litellm_enabled,
    _use_anthropic_backend,
)


# ---------------------------------------------------------------------------
# _litellm_enabled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val", ["1", "true", "yes"])
def test_litellm_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("SWARM_USE_LITELLM", val)
    assert _litellm_enabled() is True


def test_litellm_enabled_false(monkeypatch):
    monkeypatch.setenv("SWARM_USE_LITELLM", "0")
    assert _litellm_enabled() is False


def test_litellm_enabled_unset(monkeypatch):
    monkeypatch.delenv("SWARM_USE_LITELLM", raising=False)
    assert _litellm_enabled() is False


# ---------------------------------------------------------------------------
# _is_cloud_model
# ---------------------------------------------------------------------------

def test_is_cloud_model_claude():
    assert _is_cloud_model("claude-3-5-sonnet") is True


def test_is_cloud_model_anthropic_prefix():
    assert _is_cloud_model("anthropic/claude-3-haiku") is True


def test_is_cloud_model_llama():
    assert _is_cloud_model("llama3") is False


def test_is_cloud_model_gpt():
    assert _is_cloud_model("gpt-4o") is False


# ---------------------------------------------------------------------------
# _use_anthropic_backend
# ---------------------------------------------------------------------------

def test_use_anthropic_backend_openai_route():
    assert _use_anthropic_backend("claude-3-5-sonnet", "openai") is False


def test_use_anthropic_backend_anthropic_route():
    assert _use_anthropic_backend("gpt-4o", "anthropic") is True


def test_use_anthropic_backend_infer_from_model():
    assert _use_anthropic_backend("claude-3-5-sonnet", None) is True


def test_use_anthropic_backend_openai_model():
    assert _use_anthropic_backend("gpt-4o", None) is False


def test_use_anthropic_backend_empty_route():
    assert _use_anthropic_backend("claude-3-opus", "") is True


# ---------------------------------------------------------------------------
# _build_anthropic_client
# ---------------------------------------------------------------------------

def test_build_anthropic_client_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _build_anthropic_client(api_key=None)


def test_build_anthropic_client_with_key():
    with patch(
        "backend.App.integrations.infrastructure.llm.providers.Anthropic"
    ) as mock_anthropic:
        mock_anthropic.return_value = MagicMock()
        _build_anthropic_client(api_key="test-key")
    mock_anthropic.assert_called_once()


def test_build_anthropic_client_with_base_url():
    with patch(
        "backend.App.integrations.infrastructure.llm.providers.Anthropic"
    ) as mock_anthropic:
        mock_anthropic.return_value = MagicMock()
        _build_anthropic_client(api_key="test-key", base_url="https://custom.api.com")
    call_kwargs = mock_anthropic.call_args[1]
    assert "base_url" in call_kwargs
    assert call_kwargs["base_url"] == "https://custom.api.com"


def test_build_anthropic_client_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    with patch(
        "backend.App.integrations.infrastructure.llm.providers.Anthropic"
    ) as mock_anthropic:
        mock_anthropic.return_value = MagicMock()
        _build_anthropic_client()
    mock_anthropic.assert_called_once()


# ---------------------------------------------------------------------------
# _ask_anthropic
# ---------------------------------------------------------------------------

def test_ask_anthropic_basic():
    mock_content = MagicMock()
    mock_content.text = "hello from claude"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_response.usage.input_tokens = 15
    mock_response.usage.output_tokens = 8

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.providers._build_anthropic_client",
        return_value=mock_client,
    ):
        text, usage = _ask_anthropic(
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
            ],
            model="claude-3-5-sonnet",
            temperature=0.2,
            anthropic_api_key="key",
        )

    assert text == "hello from claude"
    assert usage["input_tokens"] == 15
    assert usage["output_tokens"] == 8


def test_ask_anthropic_strips_anthropic_prefix():
    mock_content = MagicMock()
    mock_content.text = "response"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_response.usage.input_tokens = 5
    mock_response.usage.output_tokens = 2

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.providers._build_anthropic_client",
        return_value=mock_client,
    ):
        text, usage = _ask_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-3-haiku",
            temperature=0.1,
        )

    # model_name should strip "anthropic/" prefix
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-3-haiku"


def test_ask_anthropic_uses_default_max_tokens(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MAX_TOKENS", raising=False)
    mock_content = MagicMock()
    mock_content.text = "test"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_response.usage.input_tokens = 1
    mock_response.usage.output_tokens = 1

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.providers._build_anthropic_client",
        return_value=mock_client,
    ):
        text, usage = _ask_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3",
            temperature=0.2,
            anthropic_api_key="k",
        )

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["max_tokens"] == 2048  # default


def test_ask_anthropic_custom_max_tokens(monkeypatch):
    mock_content = MagicMock()
    mock_content.text = "test"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_response.usage.input_tokens = 1
    mock_response.usage.output_tokens = 1

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.providers._build_anthropic_client",
        return_value=mock_client,
    ):
        text, usage = _ask_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3",
            temperature=0.2,
            anthropic_api_key="k",
            max_tokens=512,
        )

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["max_tokens"] == 512


def test_ask_anthropic_multi_content_blocks():
    c1 = MagicMock()
    c1.text = "part one "
    c2 = MagicMock()
    c2.text = "part two"
    mock_response = MagicMock()
    mock_response.content = [c1, c2]
    mock_response.usage.input_tokens = 5
    mock_response.usage.output_tokens = 3

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch(
        "backend.App.integrations.infrastructure.llm.providers._build_anthropic_client",
        return_value=mock_client,
    ):
        text, usage = _ask_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3",
            temperature=0.2,
            anthropic_api_key="k",
        )
    # Should concatenate both parts
    assert "part one" in text
    assert "part two" in text
