"""Tests for backend/App/orchestration/infrastructure/agents/base_agent.py."""
from unittest.mock import patch


from backend.App.orchestration.infrastructure.agents.base_agent import (
    BaseAgent,
    _local_base_url_from_environment,
    _strip_skill_frontmatter,
    effective_cloud_provider,
    load_prompt,
    provider_from_model,
    resolve_agent_model,
)


# ---------------------------------------------------------------------------
# effective_cloud_provider
# ---------------------------------------------------------------------------

def test_effective_cloud_provider_explicit_provider():
    result = effective_cloud_provider("openai_compatible", "cloud", "gpt-4")
    assert result == "openai_compatible"


def test_effective_cloud_provider_anthropic_env():
    result = effective_cloud_provider(None, "anthropic", "my-model")
    assert result == "anthropic"


def test_effective_cloud_provider_gemini_model():
    result = effective_cloud_provider(None, "cloud", "gemini-pro")
    assert result == "gemini"


def test_effective_cloud_provider_gpt_model():
    result = effective_cloud_provider(None, "cloud", "gpt-4o")
    assert result == "openai_compatible"


def test_effective_cloud_provider_o1_model():
    result = effective_cloud_provider(None, "cloud", "o1-preview")
    assert result == "openai_compatible"


def test_effective_cloud_provider_o3_model():
    result = effective_cloud_provider(None, "cloud", "o3-mini")
    assert result == "openai_compatible"


def test_effective_cloud_provider_openai_prefix():
    result = effective_cloud_provider(None, "cloud", "openai/gpt-4")
    assert result == "openai_compatible"


def test_effective_cloud_provider_unknown_defaults_to_anthropic():
    result = effective_cloud_provider(None, "cloud", "some-unknown-model")
    assert result == "anthropic"


def test_effective_cloud_provider_anthropic_env_with_explicit_provider():
    result = effective_cloud_provider("openai_compatible", "anthropic", "gpt-4")
    assert result == "openai_compatible"


# ---------------------------------------------------------------------------
# _strip_skill_frontmatter
# ---------------------------------------------------------------------------

def test_strip_skill_frontmatter_no_frontmatter():
    text = "hello world"
    assert _strip_skill_frontmatter(text) == "hello world"


def test_strip_skill_frontmatter_with_frontmatter():
    text = "---\ntitle: Test\n---\nbody content"
    result = _strip_skill_frontmatter(text)
    assert result == "body content"


def test_strip_skill_frontmatter_incomplete_delimiters():
    text = "---\nonly one delimiter"
    result = _strip_skill_frontmatter(text)
    # Falls back to stripped original
    assert "only one delimiter" in result


def test_strip_skill_frontmatter_empty_body():
    text = "---\ntitle: X\n---\n   "
    result = _strip_skill_frontmatter(text)
    assert result == ""


# ---------------------------------------------------------------------------
# load_prompt
# ---------------------------------------------------------------------------

def test_load_prompt_existing_file(tmp_path):
    prompt_file = tmp_path / "my_prompt.md"
    prompt_file.write_text("system prompt content", encoding="utf-8")
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.PROMPTS_DIR",
        tmp_path,
    ):
        result = load_prompt("my_prompt.md", fallback="default")
    assert result == "system prompt content"


def test_load_prompt_missing_file(tmp_path):
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.PROMPTS_DIR",
        tmp_path,
    ):
        result = load_prompt("nonexistent.md", fallback="default fallback")
    assert result == "default fallback"


def test_load_prompt_empty_file_uses_fallback(tmp_path):
    prompt_file = tmp_path / "empty.md"
    prompt_file.write_text("   ", encoding="utf-8")
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.PROMPTS_DIR",
        tmp_path,
    ):
        result = load_prompt("empty.md", fallback="fallback text")
    assert result == "fallback text"


def test_load_prompt_strips_frontmatter(tmp_path):
    prompt_file = tmp_path / "skill.md"
    prompt_file.write_text("---\ntitle: Skill\n---\nreal content", encoding="utf-8")
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.PROMPTS_DIR",
        tmp_path,
    ):
        result = load_prompt("skill.md", fallback="default")
    assert result == "real content"


# ---------------------------------------------------------------------------
# resolve_agent_model
# ---------------------------------------------------------------------------

def test_resolve_agent_model_local_default(monkeypatch):
    monkeypatch.delenv("SWARM_ROUTE_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PLANNING", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_DEFAULT", raising=False)
    monkeypatch.delenv("SWARM_MODEL_PM", raising=False)
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    result = resolve_agent_model("PM", "default-model")
    assert result == "default-model"


def test_resolve_agent_model_swarm_model_override(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "local")
    monkeypatch.setenv("SWARM_MODEL", "my-custom-model")
    monkeypatch.delenv("SWARM_MODEL_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PLANNING", raising=False)
    result = resolve_agent_model("PM", "default-model")
    assert result == "my-custom-model"


def test_resolve_agent_model_role_specific_override(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "local")
    monkeypatch.setenv("SWARM_MODEL_PM", "pm-specific-model")
    result = resolve_agent_model("PM", "default-model")
    assert result == "pm-specific-model"


def test_resolve_agent_model_cloud_route(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "cloud")
    monkeypatch.setenv("SWARM_MODEL_CLOUD", "claude-3-5-sonnet")
    monkeypatch.delenv("SWARM_MODEL_CLOUD_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PLANNING", raising=False)
    monkeypatch.delenv("SWARM_MODEL_CLOUD_PLANNING", raising=False)
    result = resolve_agent_model("PM", "default-model")
    assert result == "claude-3-5-sonnet"


def test_resolve_agent_model_cloud_role_specific(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "cloud")
    monkeypatch.setenv("SWARM_MODEL_CLOUD_DEV", "gpt-4o-mini")
    monkeypatch.delenv("SWARM_ROUTE_DEV", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_BUILD", raising=False)
    monkeypatch.delenv("SWARM_MODEL_CLOUD_BUILD", raising=False)
    result = resolve_agent_model("DEV", "default-model")
    assert result == "gpt-4o-mini"


def test_resolve_agent_model_planning_route(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_PLANNING", "local")
    monkeypatch.setenv("SWARM_MODEL_PLANNING", "planning-model")
    monkeypatch.delenv("SWARM_ROUTE_BA", raising=False)
    monkeypatch.delenv("SWARM_MODEL_BA", raising=False)
    monkeypatch.delenv("SWARM_MODEL_BA_ARCH", raising=False)
    result = resolve_agent_model("BA", "default-model")
    assert result == "planning-model"


def test_resolve_agent_model_build_route(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_BUILD", "local")
    monkeypatch.setenv("SWARM_MODEL_BUILD", "build-model")
    monkeypatch.delenv("SWARM_ROUTE_DEV", raising=False)
    monkeypatch.delenv("SWARM_MODEL_DEV", raising=False)
    result = resolve_agent_model("DEV", "default-model")
    assert result == "build-model"


def test_resolve_agent_model_ba_arch_override(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "local")
    monkeypatch.setenv("SWARM_MODEL_BA_ARCH", "ba-arch-model")
    monkeypatch.delenv("SWARM_ROUTE_ARCH", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PLANNING", raising=False)
    monkeypatch.delenv("SWARM_MODEL_ARCH", raising=False)
    result = resolve_agent_model("ARCH", "default-model")
    assert result == "ba-arch-model"


def test_resolve_agent_model_cloud_ba_arch_override(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_DEFAULT", "cloud")
    monkeypatch.setenv("SWARM_MODEL_CLOUD_BA_ARCH", "cloud-ba-arch")
    monkeypatch.delenv("SWARM_ROUTE_ARCH", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PLANNING", raising=False)
    monkeypatch.delenv("SWARM_MODEL_CLOUD_ARCH", raising=False)
    result = resolve_agent_model("ARCH", "default-model")
    assert result == "cloud-ba-arch"


def test_resolve_agent_model_prefers_workspace_models_config(monkeypatch, tmp_path):
    swarm_dir = tmp_path / ".swarm"
    swarm_dir.mkdir(parents=True)
    (swarm_dir / "models_config.json").write_text(
        '{"version":"1","roles":{"pm":{"model_id":"workspace-pm","provider":"ollama","reason":"test"}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("SWARM_MODEL_PM", raising=False)
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_PM", raising=False)
    monkeypatch.delenv("SWARM_ROUTE_DEFAULT", raising=False)

    result = resolve_agent_model("PM", "default-model")

    assert result == "workspace-pm"


# ---------------------------------------------------------------------------
# provider_from_model
# ---------------------------------------------------------------------------

def test_provider_from_model_claude():
    assert provider_from_model("claude-3-5-sonnet") == "cloud:anthropic"


def test_provider_from_model_anthropic_prefix():
    assert provider_from_model("anthropic/claude-3") == "cloud:anthropic"


def test_provider_from_model_local():
    assert provider_from_model("llama3") == "local:ollama"


def test_provider_from_model_gpt():
    assert provider_from_model("gpt-4o") == "local:ollama"


# ---------------------------------------------------------------------------
# _local_base_url_from_environment
# ---------------------------------------------------------------------------

def test_local_base_url_lmstudio(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    url, key = _local_base_url_from_environment("lmstudio")
    assert "lmstudio" in url.lower() or "1234" in url
    assert key == "lm-studio"


def test_local_base_url_ollama(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    url, key = _local_base_url_from_environment("ollama")
    assert "11434" in url or "localhost" in url
    assert key == "ollama"


def test_local_base_url_lm_studio_alias(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)
    url, key = _local_base_url_from_environment("lm_studio")
    assert key == "lm-studio"


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

def test_base_agent_effective_system_prompt_no_extra():
    agent = BaseAgent(role="dev", system_prompt="base prompt", model="llama3")
    assert agent.effective_system_prompt() == "base prompt"


def test_base_agent_effective_system_prompt_with_extra():
    agent = BaseAgent(
        role="dev", system_prompt="base", model="llama3",
        system_prompt_extra="skill content",
    )
    result = agent.effective_system_prompt()
    assert "base" in result
    assert "skill content" in result
    assert "Agent skills" in result


def test_base_agent_effective_system_prompt_whitespace_extra():
    agent = BaseAgent(role="dev", system_prompt="base", model="m", system_prompt_extra="  ")
    assert agent.effective_system_prompt() == "base"


def test_base_agent_run_ollama(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CONTEXT_TOKENS", raising=False)
    mock_response = ("model output", {"input_tokens": 10, "output_tokens": 5})
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        return_value=mock_response,
    ) as mock_ask:
        agent = BaseAgent(role="dev", system_prompt="sys", model="llama3", environment="ollama")
        result = agent.run("user prompt")
    assert result == "model output"
    assert agent.used_model == "llama3"
    assert agent.used_provider == "local:ollama"
    mock_ask.assert_called_once()


def test_base_agent_run_lmstudio(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CONTEXT_TOKENS", raising=False)
    mock_response = ("lmstudio output", {})
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        return_value=mock_response,
    ):
        agent = BaseAgent(role="dev", system_prompt="sys", model="phi-4", environment="lmstudio")
        result = agent.run("user prompt")
    assert result == "lmstudio output"
    assert agent.used_provider == "local:lmstudio"


def test_base_agent_run_cloud_anthropic(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CONTEXT_TOKENS", raising=False)
    mock_response = ("cloud output", {})
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        return_value=mock_response,
    ), patch(
        "backend.App.orchestration.infrastructure.agents.llm_backend_selector.uses_anthropic_sdk",
        return_value=True,
    ):
        agent = BaseAgent(
            role="dev", system_prompt="sys", model="claude-3-5-sonnet",
            environment="cloud", remote_api_key="key123",
        )
        result = agent.run("user prompt")
    assert result == "cloud output"
    assert agent.used_provider == "cloud:anthropic"


def test_base_agent_run_cloud_openai_compat(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CONTEXT_TOKENS", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    mock_response = ("openai output", {})
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        return_value=mock_response,
    ), patch(
        "backend.App.orchestration.infrastructure.agents.llm_backend_selector.uses_anthropic_sdk",
        return_value=False,
    ), patch(
        "backend.App.orchestration.infrastructure.agents.llm_backend_selector.resolve_openai_compat_base_url",
        return_value="https://api.openai.com/v1",
    ):
        agent = BaseAgent(
            role="dev", system_prompt="sys", model="gpt-4o",
            environment="cloud",
        )
        result = agent.run("user prompt")
    assert result == "openai output"
    assert "openai_compatible" in agent.used_provider or "cloud:" in agent.used_provider


def test_base_agent_run_context_tokens_warning(monkeypatch, caplog):
    monkeypatch.setenv("SWARM_LLM_CONTEXT_TOKENS", "100")
    mock_response = ("output", {})
    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        return_value=mock_response,
    ):
        agent = BaseAgent(role="dev", system_prompt="sys", model="m",
                          environment="ollama")
        # Large prompt to trigger warning
        agent.run("x" * 5000)
    # No assertion on warning text — just ensure it doesn't raise


def test_base_agent_run_with_max_tokens(monkeypatch):
    monkeypatch.delenv("SWARM_LLM_CONTEXT_TOKENS", raising=False)
    mock_response = ("output", {})
    captured_kwargs = {}

    def mock_ask_model(messages, model, **kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    with patch(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        side_effect=mock_ask_model,
    ):
        agent = BaseAgent(
            role="dev", system_prompt="sys", model="llama3",
            environment="ollama", max_tokens=512,
        )
        agent.run("prompt")
    assert captured_kwargs.get("max_tokens") == 512
