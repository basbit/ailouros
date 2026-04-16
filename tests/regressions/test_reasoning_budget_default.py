"""Regression: reasoning models must have a default thinking_budget cap.

Bug aec02899 ROOT CAUSE: ``qwen3.5-9b-ud-mlx`` was identified as a
reasoning model but ``SWARM_LOCAL_LLM_REASONING_BUDGET`` was unset —
so the cap resolver returned None and the model entered an unbounded
thinking loop (3+ hours, 108k+ tokens, no output).

Review-rules §2: fail fast by default. A known-reasoning model with no
budget is a silent fallback ("continue thinking forever") — must be
replaced with a deterministic default that the user can explicitly
opt out of.

Expected:
  * Reasoning model on local URL + env unset → default cap (positive int).
  * Reasoning model on local URL + env="off"/"none"/"0" → None (opt-out).
  * Reasoning model on local URL + env=<positive int> → that int.
  * Non-reasoning model → None (no cap, no reasoning loop expected).
  * Cloud URL → None (cloud has its own budget controls).
"""
from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.llm.router import (
    _DEFAULT_REASONING_BUDGET_TOKENS,
    _is_reasoning_model,
    _local_llm_reasoning_budget,
)


_LOCAL = "http://localhost:1234/v1"
_CLOUD = "https://api.anthropic.com/v1"


@pytest.mark.parametrize(
    "model",
    [
        "qwen3.5-9b-ud-mlx",    # the bug model
        "qwen3:8b",
        "qwen-3-coder",
        "deepseek-r1:8b",
        "deepseek-r1-distill",
        "some-thinking-model",
        "qwq-reasoning",
    ],
)
def test_reasoning_model_keywords_match(model):
    assert _is_reasoning_model(model), f"{model} must be detected as reasoning"


@pytest.mark.parametrize(
    "model",
    ["gpt-4", "llama3:8b", "claude-3-5-sonnet", "mistral:7b", ""],
)
def test_non_reasoning_model_not_matched(model):
    assert not _is_reasoning_model(model)


def test_default_cap_applied_for_reasoning_model_when_env_unset(monkeypatch):
    """Root-cause regression: cap MUST have a finite default (was None)."""
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    budget = _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL)
    assert budget == _DEFAULT_REASONING_BUDGET_TOKENS
    assert budget is not None and budget > 0


def test_explicit_env_value_wins(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_REASONING_BUDGET", "8192")
    assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) == 8192


@pytest.mark.parametrize("optout", ["off", "none", "0", "disabled", "unlimited"])
def test_explicit_optout_returns_none(monkeypatch, optout):
    monkeypatch.setenv("SWARM_LOCAL_LLM_REASONING_BUDGET", optout)
    assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) is None


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SWARM_LOCAL_LLM_REASONING_BUDGET", "banana")
    budget = _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL)
    assert budget == _DEFAULT_REASONING_BUDGET_TOKENS


def test_negative_env_returns_none(monkeypatch):
    """Negative explicit value is treated as 'opt-out via invalid' — not default."""
    monkeypatch.setenv("SWARM_LOCAL_LLM_REASONING_BUDGET", "-100")
    # Negative: resolver returns None (not default) since value parsed but <=0
    assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) is None


def test_non_reasoning_model_no_cap(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    assert _local_llm_reasoning_budget("llama3:8b", _LOCAL) is None


def test_cloud_url_no_cap(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    # Reasoning model name but cloud URL → no local cap applied
    assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _CLOUD) is None


# ---------------------------------------------------------------------------
# §23.2 — role-aware budget via ContextBudget, pinned by step_decorator
# ---------------------------------------------------------------------------


def test_step_context_overrides_default_budget(monkeypatch):
    """When a pipeline step is pinned via current_step(...), the resolver
    uses the per-role reasoning_budget_tokens from the ContextBudget
    profile, not the global default. (Dev = 1024.)"""
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    from backend.App.orchestration.application.current_step import current_step

    with current_step("dev"):
        assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) == 1024


def test_step_context_review_tier_budget(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    from backend.App.orchestration.application.current_step import current_step

    with current_step("review_dev"):
        assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) == 2048


def test_step_context_architecture_keeps_full_cap(monkeypatch):
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    from backend.App.orchestration.application.current_step import current_step

    with current_step("architect"):
        assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) == 4096


def test_env_override_wins_over_step_context(monkeypatch):
    """Operators setting a global SWARM_LOCAL_LLM_REASONING_BUDGET must
    still win over per-step defaults — matches the documented resolution
    order (env > step context > default)."""
    monkeypatch.setenv("SWARM_LOCAL_LLM_REASONING_BUDGET", "8192")
    from backend.App.orchestration.application.current_step import current_step

    with current_step("dev"):
        assert _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL) == 8192


def test_no_step_context_falls_back_to_default(monkeypatch):
    """Outside a step (CLI / tests / background threads) the resolver
    returns the global default — no ContextBudget lookup possible."""
    monkeypatch.delenv("SWARM_LOCAL_LLM_REASONING_BUDGET", raising=False)
    # No current_step() wrapping at all:
    assert (
        _local_llm_reasoning_budget("qwen3.5-9b-ud-mlx", _LOCAL)
        == _DEFAULT_REASONING_BUDGET_TOKENS
    )
