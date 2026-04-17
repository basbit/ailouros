"""Tests for AgenticBaseAgent tool-loop backend routing."""

from __future__ import annotations

from types import SimpleNamespace


def test_local_openai_compat_tools_use_openai_loop(monkeypatch) -> None:
    from backend.App.orchestration.infrastructure.agents.agentic_base_agent import (
        AgenticBaseAgent,
    )

    cfg = SimpleNamespace(
        llm_route="",
        provider_label="local:ollama",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )

    class FakeSelector:
        def select(self, **_kwargs):
            return cfg

        def ask_kwargs(self, _cfg):
            return {}

    monkeypatch.setattr(
        "backend.App.orchestration.infrastructure.agents.llm_backend_selector.LLMBackendSelector",
        FakeSelector,
    )

    called: dict[str, object] = {}

    def fake_openai_loop(self, cfg_arg, messages, max_rounds, progress_queue):
        called["cfg"] = cfg_arg
        called["messages"] = messages
        called["max_rounds"] = max_rounds
        called["progress_queue"] = progress_queue
        return "tool-loop-ok"

    monkeypatch.setattr(AgenticBaseAgent, "_run_openai_tool_loop", fake_openai_loop)

    agent = AgenticBaseAgent(
        role="SOURCE_RESEARCH",
        system_prompt="system",
        model="qwen2.5",
        environment="ollama",
    )
    agent.register_tool(
        "web_search",
        lambda _args: "[]",
        description="Search the web",
        input_schema={"type": "object", "properties": {}},
    )

    result = agent.run("Find data")

    assert result == "tool-loop-ok"
    assert called["cfg"] is cfg
    assert called["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "Find data"},
    ]
