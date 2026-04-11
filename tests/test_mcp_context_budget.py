"""SWARM_MODEL_CONTEXT_SIZE: user_content budget calculation."""
from backend.App.integrations.infrastructure.mcp.openai_loop.context_manager import (
    compute_user_content_budget_from_env,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _model_context_reserve_tokens,
    _model_context_size_tokens,
)


def test_context_size_not_set_returns_default(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_SIZE", raising=False)
    assert _model_context_size_tokens() == 16384  # safe default for modern local models


def test_context_size_set(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    assert _model_context_size_tokens() == 4096


def test_context_size_invalid_returns_default(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "notanumber")
    assert _model_context_size_tokens() == 16384  # fallback to default


def test_reserve_default(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", raising=False)
    assert _model_context_reserve_tokens() == 1024


def test_reserve_override(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "256")
    assert _model_context_reserve_tokens() == 256


def test_budget_positive_with_default_context(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_SIZE", raising=False)
    budget = compute_user_content_budget_from_env("sys", [])
    assert budget > 0  # default 16384 tokens → positive budget


def test_budget_positive_with_small_context(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "512")
    budget = compute_user_content_budget_from_env("s" * 300, [])
    assert budget > 0


def test_budget_smaller_with_tool_schemas(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "512")
    tools_with_desc = [
        {"function": {"description": "d" * 600, "parameters": {}}}
        for _ in range(5)
    ]
    budget_no_tools = compute_user_content_budget_from_env("s", [])
    budget_with_tools = compute_user_content_budget_from_env("s", tools_with_desc)
    assert budget_with_tools < budget_no_tools


def test_budget_zero_when_system_prompt_fills_context(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "100")
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "50")
    # system prompt of 200 chars / 3 ≈ 67 tokens; context is 100, reserve 50 → available ≤ 0
    budget = compute_user_content_budget_from_env("x" * 200, [])
    assert budget == 0
