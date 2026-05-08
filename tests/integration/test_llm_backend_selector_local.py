"""Tests for the 'local' / 'llamacpp' branch of LLMBackendSelector."""

from __future__ import annotations

import pytest

from backend.App.orchestration.infrastructure.agents.llm_backend_selector import (
    LLMBackendSelector,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "AILOUROS_LLM_BASE_URL",
        "AILOUROS_LLM_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_API_KEY",
        "LMSTUDIO_BASE_URL",
        "LMSTUDIO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_local_env_uses_ailouros_llm_url(monkeypatch):
    monkeypatch.setenv("AILOUROS_LLM_BASE_URL", "http://localhost:9090/v1")
    monkeypatch.setenv("AILOUROS_LLM_API_KEY", "secret-token")
    cfg = LLMBackendSelector().select(
        role="dev",
        model="local-default",
        environment="local",
    )
    assert cfg.base_url == "http://localhost:9090/v1"
    assert cfg.api_key == "secret-token"
    assert cfg.llm_route == "openai"
    assert cfg.provider_label == "local:llamacpp"


def test_local_env_falls_back_to_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    cfg = LLMBackendSelector().select(
        role="dev",
        model="local-default",
        environment="llamacpp",
    )
    assert cfg.base_url == "http://localhost:8080/v1"
    assert cfg.llm_route == "openai"


def test_local_env_default_url_when_nothing_set():
    cfg = LLMBackendSelector().select(
        role="dev",
        model="local-default",
        environment="local",
    )
    assert cfg.base_url == "http://localhost:8080/v1"
    assert cfg.api_key == "sk-no-key-required"


def test_ollama_env_no_longer_reads_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    cfg = LLMBackendSelector().select(
        role="dev",
        model="qwen2.5",
        environment="ollama",
    )
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.provider_label == "local:ollama"


def test_lmstudio_env_unchanged(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    cfg = LLMBackendSelector().select(
        role="dev",
        model="anything",
        environment="lmstudio",
    )
    assert cfg.base_url == "http://localhost:1234/v1"
    assert cfg.provider_label == "local:lmstudio"
