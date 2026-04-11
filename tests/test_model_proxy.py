"""Tests for backend/App/integrations/infrastructure/model_proxy.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from backend.App.integrations.infrastructure.model_proxy import (
    _anthropic_models_list,
    _fmt_ctx,
    _format_capabilities_display,
    _openai_model_row,
    normalize_ollama_tags_payload,
    normalize_openai_v1_models_payload,
    remote_openai_compatible_models_dict,
)


# ---------------------------------------------------------------------------
# _format_capabilities_display
# ---------------------------------------------------------------------------

def test_format_capabilities_display_none():
    assert _format_capabilities_display(None) == ""


def test_format_capabilities_display_list():
    result = _format_capabilities_display(["text", "vision"])
    assert "text" in result
    assert "vision" in result


def test_format_capabilities_display_list_with_none():
    result = _format_capabilities_display(["text", None, ""])
    assert "text" in result
    assert "None" not in result


def test_format_capabilities_display_dict_true_values():
    result = _format_capabilities_display({"text": True, "vision": False, "audio": True})
    assert "text" in result
    assert "audio" in result
    assert "vision" not in result


def test_format_capabilities_display_dict_empty():
    result = _format_capabilities_display({})
    assert result == ""


def test_format_capabilities_display_string():
    result = _format_capabilities_display("custom-cap")
    assert result == "custom-cap"


def test_format_capabilities_display_empty_list():
    result = _format_capabilities_display([])
    assert result == ""


# ---------------------------------------------------------------------------
# _fmt_ctx
# ---------------------------------------------------------------------------

def test_fmt_ctx_millions():
    assert _fmt_ctx(2_000_000) == "2M ctx"


def test_fmt_ctx_thousands():
    assert _fmt_ctx(131_072) == "131k ctx"


def test_fmt_ctx_small():
    assert _fmt_ctx(512) == "512 ctx"


def test_fmt_ctx_none():
    assert _fmt_ctx(None) == ""


def test_fmt_ctx_invalid_string():
    assert _fmt_ctx("not-a-number") == ""


def test_fmt_ctx_zero():
    assert _fmt_ctx(0) == "0 ctx"


def test_fmt_ctx_exactly_1000():
    assert _fmt_ctx(1000) == "1k ctx"


def test_fmt_ctx_string_number():
    # Should handle string-encoded ints
    result = _fmt_ctx("8192")
    assert "8k" in result


# ---------------------------------------------------------------------------
# _openai_model_row
# ---------------------------------------------------------------------------

def test_openai_model_row_basic():
    item = {"id": "gpt-4o", "context_window": 128_000}
    row = _openai_model_row(item)
    assert row is not None
    assert row["id"] == "gpt-4o"
    assert "128k" in row["label"]


def test_openai_model_row_empty_id():
    row = _openai_model_row({"id": "", "context_window": 1000})
    assert row is None


def test_openai_model_row_missing_id():
    row = _openai_model_row({"context_window": 1000})
    assert row is None


def test_openai_model_row_with_capabilities_list():
    item = {"id": "llama3", "capabilities": ["text", "vision"], "context_window": 4096}
    row = _openai_model_row(item)
    assert row is not None
    assert "text" in row["label"] or "vision" in row["label"]


def test_openai_model_row_with_context_length_fallback():
    item = {"id": "model-x", "context_length": 32_000}
    row = _openai_model_row(item)
    assert row is not None
    assert row["context_window"] == 32_000


def test_openai_model_row_no_context():
    item = {"id": "bare-model"}
    row = _openai_model_row(item)
    assert row is not None
    assert row["id"] == "bare-model"
    assert row["label"] == "bare-model"


def test_openai_model_row_capabilities_uppercase_key():
    item = {"id": "model-y", "Capabilities": {"text": True}}
    row = _openai_model_row(item)
    assert row is not None
    assert "text" in row["label"]


# ---------------------------------------------------------------------------
# _anthropic_models_list
# ---------------------------------------------------------------------------

def test_anthropic_models_list_structure():
    result = _anthropic_models_list()
    assert result["ok"] is True
    assert result["source"] == "built-in"
    assert len(result["models"]) > 0


def test_anthropic_models_list_has_required_fields():
    result = _anthropic_models_list()
    for m in result["models"]:
        assert "id" in m
        assert "label" in m
        assert "context_window" in m


def test_anthropic_models_list_context_in_label():
    result = _anthropic_models_list()
    # All models have 200k context
    for m in result["models"]:
        assert "200k" in m["label"] or m["context_window"] == 200_000


def test_anthropic_models_list_known_models():
    result = _anthropic_models_list()
    ids = [m["id"] for m in result["models"]]
    assert "claude-3-5-sonnet-latest" in ids


# ---------------------------------------------------------------------------
# normalize_openai_v1_models_payload
# ---------------------------------------------------------------------------

def test_normalize_openai_v1_models_empty():
    result = normalize_openai_v1_models_payload({})
    assert result == []


def test_normalize_openai_v1_models_empty_data():
    result = normalize_openai_v1_models_payload({"data": []})
    assert result == []


def test_normalize_openai_v1_models_basic():
    payload = {"data": [{"id": "llama3", "context_window": 8192}]}
    result = normalize_openai_v1_models_payload(payload)
    assert len(result) == 1
    assert result[0]["id"] == "llama3"


def test_normalize_openai_v1_models_skips_invalid():
    payload = {"data": [{"id": "valid"}, "not-a-dict", None]}
    result = normalize_openai_v1_models_payload(payload)
    assert len(result) == 1


def test_normalize_openai_v1_models_skips_empty_id():
    payload = {"data": [{"id": ""}, {"id": "  "}]}
    result = normalize_openai_v1_models_payload(payload)
    assert result == []


def test_normalize_openai_v1_models_multiple():
    payload = {
        "data": [
            {"id": "gpt-4o", "context_window": 128_000},
            {"id": "gpt-3.5-turbo", "context_window": 16_000},
        ]
    }
    result = normalize_openai_v1_models_payload(payload)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# normalize_ollama_tags_payload
# ---------------------------------------------------------------------------

def test_normalize_ollama_tags_empty():
    result = normalize_ollama_tags_payload({})
    assert result == []


def test_normalize_ollama_tags_basic():
    payload = {"models": [{"name": "llama3", "context_length": 8192}]}
    result = normalize_ollama_tags_payload(payload)
    assert len(result) == 1
    assert result[0]["id"] == "llama3"
    assert "8k" in result[0]["label"]


def test_normalize_ollama_tags_model_key_fallback():
    payload = {"models": [{"model": "phi3"}]}
    result = normalize_ollama_tags_payload(payload)
    assert result[0]["id"] == "phi3"


def test_normalize_ollama_tags_skips_empty_name():
    payload = {"models": [{"name": ""}, {"name": "  "}]}
    result = normalize_ollama_tags_payload(payload)
    assert result == []


def test_normalize_ollama_tags_skips_non_dicts():
    payload = {"models": ["not-a-dict", None, {"name": "real-model"}]}
    result = normalize_ollama_tags_payload(payload)
    assert len(result) == 1


def test_normalize_ollama_tags_model_info_context():
    payload = {
        "models": [
            {
                "name": "llama3",
                "model_info": {"llama.context_length": 4096},
            }
        ]
    }
    result = normalize_ollama_tags_payload(payload)
    assert result[0]["context_window"] == 4096


def test_normalize_ollama_tags_with_capabilities():
    payload = {
        "models": [
            {"name": "llava", "capabilities": ["text", "vision"]}
        ]
    }
    result = normalize_ollama_tags_payload(payload)
    assert "text" in result[0]["label"] or "vision" in result[0]["label"]


def test_normalize_ollama_tags_capabilities_uppercase():
    payload = {
        "models": [
            {"name": "model-x", "Capabilities": {"embed": True}}
        ]
    }
    result = normalize_ollama_tags_payload(payload)
    assert result[0]["id"] == "model-x"


# ---------------------------------------------------------------------------
# remote_openai_compatible_models_dict
# ---------------------------------------------------------------------------

def test_remote_models_dict_unsupported_provider():
    result = remote_openai_compatible_models_dict(provider="unknown_provider")
    assert result["ok"] is False
    assert "models" in result
    assert result["models"] == []


def test_remote_models_dict_anthropic_builtin():
    result = remote_openai_compatible_models_dict(provider="anthropic")
    assert result["ok"] is True
    assert len(result["models"]) > 0
    assert result["source"] == "built-in"


def test_remote_models_dict_ollama_cloud_no_base_url():
    result = remote_openai_compatible_models_dict(provider="ollama_cloud")
    assert result["ok"] is False
    assert "base URL" in result["error"] or "base" in result["error"].lower()


def test_remote_models_dict_openai_compat_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "my-model"}]}
    mock_response.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.model_proxy.httpx.Client", return_value=mock_client):
        result = remote_openai_compatible_models_dict(
            provider="openai_compatible",
            base_url="http://localhost:11434/v1",
        )
    assert result["ok"] is True
    assert len(result["models"]) == 1


def test_remote_models_dict_openai_compat_http_error():
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = Exception("connection refused")

    with patch("backend.App.integrations.infrastructure.model_proxy.httpx.Client", return_value=mock_client):
        result = remote_openai_compatible_models_dict(
            provider="openai_compatible",
            base_url="http://localhost:11434/v1",
        )
    assert result["ok"] is False
    assert "error" in result


def test_remote_models_dict_with_api_key():
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "model-x"}]}
    mock_response.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.model_proxy.httpx.Client", return_value=mock_client):
        result = remote_openai_compatible_models_dict(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
        )

    call_kwargs = mock_client.get.call_args
    assert call_kwargs is not None
    assert result["ok"] is True


def test_remote_models_dict_groq_provider():
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "llama3-70b-groq"}]}
    mock_response.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response

    with patch("backend.App.integrations.infrastructure.model_proxy.httpx.Client", return_value=mock_client):
        result = remote_openai_compatible_models_dict(
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key="groq-key",
        )
    assert result["ok"] is True
