"""Пресеты base URL для удалённых OpenAI-compatible провайдеров."""

from backend.App.integrations.infrastructure.llm.remote_presets import (
    default_openai_compat_base_url,
    resolve_openai_compat_base_url,
    uses_anthropic_sdk,
)


def test_uses_anthropic_sdk():
    assert uses_anthropic_sdk("anthropic") is True
    assert uses_anthropic_sdk("Anthropic ") is True
    assert uses_anthropic_sdk("groq") is False


def test_default_openai_compat_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_OPENAI_BASE_URL", raising=False)
    assert "openai.com" in default_openai_compat_base_url("openai_compatible")
    assert "generativelanguage.googleapis.com" in default_openai_compat_base_url("gemini")
    assert "groq.com" in default_openai_compat_base_url("groq")
    assert "cerebras.ai" in default_openai_compat_base_url("cerebras")
    assert "openrouter.ai" in default_openai_compat_base_url("openrouter")
    assert "deepseek.com" in default_openai_compat_base_url("deepseek")
    assert default_openai_compat_base_url("ollama_cloud") == ""


def test_resolve_openai_compat_base_url_gemini_ignores_stale_openai_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    wrong = "https://api.openai.com/v1"
    out = resolve_openai_compat_base_url("gemini", wrong)
    assert "generativelanguage.googleapis.com" in out
    assert "openai.com" not in out


def test_resolve_openai_compat_base_url_gemini_keeps_google_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    custom = "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert resolve_openai_compat_base_url("gemini", custom) == custom


def test_resolve_gemini_v1_root_replaced_with_openai_compat_default(monkeypatch):
    """/v1/ is not OpenAI shim — client would POST .../v1/chat/completions → 404."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    wrong = "https://generativelanguage.googleapis.com/v1/"
    out = resolve_openai_compat_base_url("gemini", wrong)
    assert "/v1beta/openai" in out


def test_resolve_openai_compat_openai_ignores_gemini_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    wrong = "https://generativelanguage.googleapis.com/v1beta/openai/"
    out = resolve_openai_compat_base_url("openai_compatible", wrong)
    assert "openai.com" in out


def test_resolve_groq_ignores_stale_openai_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    out = resolve_openai_compat_base_url("groq", "https://api.openai.com/v1")
    assert "groq.com" in out


def test_resolve_cerebras_ignores_stale_gemini_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    out = resolve_openai_compat_base_url(
        "cerebras",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    assert "cerebras.ai" in out


def test_resolve_deepseek_ignores_stale_groq_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    out = resolve_openai_compat_base_url("deepseek", "https://api.groq.com/openai/v1")
    assert "deepseek.com" in out


def test_resolve_openrouter_ignores_stale_openai_host(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    out = resolve_openai_compat_base_url("openrouter", "https://api.openai.com/v1")
    assert "openrouter.ai" in out


def test_resolve_keeps_unknown_host_for_groq(monkeypatch):
    """Кастомный прокси / неизвестный хост не перезаписываем."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    custom = "https://llm-gateway.internal.corp/v1"
    assert resolve_openai_compat_base_url("groq", custom) == custom


def test_resolve_ollama_cloud_empty_no_openai_fallback(monkeypatch):
    monkeypatch.delenv("OLLAMA_CLOUD_OPENAI_BASE_URL", raising=False)
    assert resolve_openai_compat_base_url("ollama_cloud", "") == ""
    assert resolve_openai_compat_base_url("ollama_cloud", None) == ""
