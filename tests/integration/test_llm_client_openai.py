"""Поведение OpenAI SDK / httpx для локальных LLM (без опасных повторов на таймаут)."""

from backend.App.integrations.infrastructure.llm.client import (
    _local_llm_serialize_http_enabled,
    _resolve_openai_max_retries,
    make_openai_client,
    merge_openai_compat_max_tokens,
)


def test_resolve_openai_max_retries_localhost_zero():
    assert _resolve_openai_max_retries("http://127.0.0.1:11434/v1") == 0
    assert _resolve_openai_max_retries("http://localhost:1234/v1") == 0
    assert _resolve_openai_max_retries("http://host.docker.internal:11434/v1") == 0


def test_resolve_openai_max_retries_remote_default_two():
    assert _resolve_openai_max_retries("https://api.openai.com/v1") == 2
    assert _resolve_openai_max_retries("https://api.example.com/v1") == 2


def test_resolve_openai_max_retries_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_MAX_RETRIES", "1")
    assert _resolve_openai_max_retries("http://127.0.0.1:11434/v1") == 1


def test_make_openai_client_local_has_zero_retries():
    c = make_openai_client(base_url="http://localhost:11434/v1", api_key="ollama")
    assert c.max_retries == 0
    assert c._client is not None


def test_local_llm_serialize_off_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_SERIALIZE", raising=False)
    assert _local_llm_serialize_http_enabled("http://127.0.0.1:11434/v1") is False


def test_local_llm_serialize_on_localhost(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_SERIALIZE", "1")
    assert _local_llm_serialize_http_enabled("http://127.0.0.1:11434/v1") is True
    assert _local_llm_serialize_http_enabled("https://api.openai.com/v1") is False


def test_merge_openai_compat_max_tokens_remote_default(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", raising=False)
    d = merge_openai_compat_max_tokens({"model": "x"}, base_url="https://openrouter.ai/api/v1")
    assert d["max_tokens"] == 4096


def test_merge_openai_compat_max_tokens_local_unchanged(monkeypatch):
    monkeypatch.delenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", raising=False)
    d = merge_openai_compat_max_tokens({"model": "x"}, base_url="http://127.0.0.1:11434/v1")
    assert "max_tokens" not in d


def test_merge_openai_compat_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", "2048")
    d = merge_openai_compat_max_tokens({"model": "x"}, base_url="https://api.example.com/v1")
    assert d["max_tokens"] == 2048


def test_merge_openai_compat_max_tokens_respects_existing(monkeypatch):
    monkeypatch.setenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", "999")
    d = merge_openai_compat_max_tokens(
        {"model": "x", "max_tokens": 50},
        base_url="https://api.example.com/v1",
    )
    assert d["max_tokens"] == 50
