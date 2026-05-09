from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.integrations.application.runtime_capabilities import (
    LlmReadiness,
    evaluate_llm_readiness,
    probe_capabilities,
)
from backend.App.orchestration.application.use_cases.start_pipeline_run import (
    _has_role_level_llm_override,
)


_LLM_PROVIDER_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "CEREBRAS_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AILOUROS_MODELS_DIR", raising=False)
    monkeypatch.delenv("AILOUROS_LLM_PROVIDER_PROFILE_OVERRIDE", raising=False)
    for name in _LLM_PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_evaluate_returns_not_ready_when_nothing_configured() -> None:
    state = evaluate_llm_readiness()

    assert state == LlmReadiness(
        ready=False,
        reason=(
            "no local GGUF model under AILOUROS_MODELS_DIR and no cloud provider key set; "
            "download a model in onboarding or configure Ollama/LM Studio/cloud"
        ),
    )


def test_evaluate_ready_when_local_gguf_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "gemma-4-q4.gguf").write_bytes(b"\x00")
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(models_dir))

    state = evaluate_llm_readiness()

    assert state.ready is True
    assert "GGUF" in state.reason


def test_evaluate_ready_with_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-anything")

    state = evaluate_llm_readiness()

    assert state.ready is True
    assert "cloud" in state.reason


def test_evaluate_ignores_blank_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    state = evaluate_llm_readiness()

    assert state.ready is False


def test_evaluate_ready_when_operator_override_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AILOUROS_LLM_PROVIDER_PROFILE_OVERRIDE", "1")

    state = evaluate_llm_readiness()

    assert state.ready is True
    assert "override" in state.reason.lower()


def test_probe_capabilities_includes_local_llm_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    probes = probe_capabilities()
    by_name = {probe.name: probe for probe in probes}

    assert "local_llm" in by_name
    assert by_name["local_llm"].ready is False


def test_role_override_detected_for_ollama_role() -> None:
    agent_config = {
        "swarm": {"topology": "linear"},
        "pm": {"environment": "ollama", "model": "qwen2.5:7b"},
    }

    assert _has_role_level_llm_override(agent_config) is True


def test_role_override_ignored_when_model_blank() -> None:
    agent_config = {
        "pm": {"environment": "ollama", "model": ""},
    }

    assert _has_role_level_llm_override(agent_config) is False


def test_role_override_ignored_for_unknown_environment() -> None:
    agent_config = {
        "pm": {"environment": "custom-thing", "model": "abc"},
    }

    assert _has_role_level_llm_override(agent_config) is False


def test_role_override_skips_swarm_section() -> None:
    agent_config = {
        "swarm": {"environment": "ollama", "model": "fake"},
    }

    assert _has_role_level_llm_override(agent_config) is False
