from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
from backend.App.orchestration.infrastructure.agents.base_agent import load_prompt, resolve_agent_model


def test_pm_agent_calls_llm(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL", "test-model")
    captured = {}

    def fake_ask_model(messages, model, temperature=0.2, **kwargs):
        captured["messages"] = messages
        captured["model"] = model
        return ("pm ok", {"input_tokens": 0, "output_tokens": 0, "model": model, "cached": False})

    monkeypatch.setattr("backend.App.orchestration.infrastructure.agents.base_agent.ask_model", fake_ask_model)
    output = PMAgent().run("Create a landing page")
    assert output == "pm ok"
    assert captured["model"] == "test-model"
    assert captured["messages"][1]["content"] == "Create a landing page"


def test_all_agents_return_string(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL", "test-model")
    monkeypatch.setattr(
        "backend.App.orchestration.infrastructure.agents.base_agent.ask_model",
        lambda *args, **kwargs: ("ok", {"input_tokens": 0, "output_tokens": 0, "model": "", "cached": False}),
    )
    agents = [PMAgent(), BAAgent(), ArchitectAgent(), DevAgent(), QAAgent()]
    for agent in agents:
        result = agent.run("input")
        assert isinstance(result, str)
        assert result == "ok"


def test_prompt_loader_reads_file():
    loaded = load_prompt(
        "project-management/project-manager-senior.md",
        "fallback-prompt",
    )
    # YAML frontmatter (SKILL-style) убирается из текста для LLM
    assert "SeniorProjectManager" in loaded
    assert "# Project Manager Agent Personality" in loaded
    assert loaded != "fallback-prompt"


def test_ba_default_prompt_from_prompts_dir():
    loaded = load_prompt(
        "product/product-requirements-analyst.md",
        "fallback",
    )
    assert "Architect" in loaded
    assert loaded != "fallback"


def test_qa_default_software_prompt_from_prompts_dir():
    loaded = load_prompt(
        "specialized/software-qa-engineer.md",
        "fallback",
    )
    assert "Software QA Engineer" in loaded or "тестированию ПО" in loaded
    assert loaded != "fallback"


def test_role_model_override(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL", raising=False)
    monkeypatch.setenv("SWARM_MODEL_PLANNING", "deepseek-r1:14b")
    monkeypatch.setenv("SWARM_MODEL_DEV", "qwen3-coder:14b")
    # PM does not inherit SWARM_MODEL_PLANNING — only per-role key or deprecated fallback
    assert resolve_agent_model("PM", "deepseek-coder:14b") == "deepseek-coder:14b"
    monkeypatch.setenv("SWARM_MODEL_PM", "big-pm-model")
    assert resolve_agent_model("PM", "x") == "big-pm-model"
    assert resolve_agent_model("BA", "x") == "deepseek-r1:14b"
    assert resolve_agent_model("DEV", "qwen3-coder:30b") == "qwen3-coder:14b"


def test_cloud_routing(monkeypatch):
    monkeypatch.setenv("SWARM_ROUTE_PLANNING", "cloud")
    monkeypatch.setenv("SWARM_MODEL_CLOUD_PLANNING", "claude-3-5-sonnet-latest")
    assert resolve_agent_model("PM", "deepseek-r1:14b") == "claude-3-5-sonnet-latest"
