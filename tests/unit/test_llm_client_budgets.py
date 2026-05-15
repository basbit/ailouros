from __future__ import annotations

import pytest

from backend.App.integrations.domain.role_budgets import (
    BudgetExceededError,
    RoleBudget,
)
from backend.App.integrations.infrastructure.llm import client as llm_client
from backend.App.integrations.infrastructure.llm import role_budget_enforcer
from backend.App.integrations.infrastructure import role_budgets_loader


@pytest.fixture
def _stub_budget(monkeypatch):
    budget = RoleBudget(
        prompt_tokens_max=100,
        reasoning_tokens_max=2048,
        completion_tokens_max=64,
        total_tokens_ceiling=4096,
    )
    monkeypatch.setattr(
        role_budgets_loader, "load_role_budgets", lambda: {"pm": budget}
    )
    monkeypatch.setattr(
        role_budget_enforcer, "get_role_budget", lambda role: {"pm": budget}.get(role)
    )
    return budget


def _capture_provider(captured, *, text="ok", input_tokens=10, output_tokens=10):
    def fn(**kwargs):
        captured.update(kwargs)
        return text, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": kwargs.get("model"),
            "cached": False,
        }
    return fn


def test_ask_model_prompt_cap_raises_before_send(monkeypatch, _stub_budget):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )

    called = {}

    def fail_anthropic(**kwargs):
        called["yes"] = True
        return "x", {}

    monkeypatch.setattr(llm_client, "_ask_anthropic", fail_anthropic)

    long_text = "x" * 100_000
    with pytest.raises(BudgetExceededError) as exc:
        llm_client.ask_model(
            messages=[{"role": "user", "content": long_text}],
            model="claude-3-5-sonnet-latest",
            role="pm",
        )
    assert exc.value.channel == "prompt_tokens"
    assert "yes" not in called


def test_ask_model_unknown_role_raises(monkeypatch, _stub_budget):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )

    with pytest.raises(BudgetExceededError) as exc:
        llm_client.ask_model(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3-5-sonnet-latest",
            role="totally_unknown_role",
        )
    assert exc.value.channel == "role"


def test_ask_model_anthropic_path_injects_thinking_and_caps(monkeypatch, _stub_budget):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )

    captured: dict = {}
    monkeypatch.setattr(
        llm_client,
        "_ask_anthropic",
        _capture_provider(captured, text="short reply", output_tokens=2),
    )

    text, _usage = llm_client.ask_model(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-3-5-sonnet-latest",
        role="pm",
    )
    assert text == "short reply"
    assert captured["thinking"] == {
        "type": "enabled",
        "budget_tokens": 2048,
    }
    assert captured["max_tokens"] == 64


def test_ask_model_anthropic_respects_caller_smaller_max_tokens(
    monkeypatch, _stub_budget
):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )
    captured: dict = {}
    monkeypatch.setattr(
        llm_client,
        "_ask_anthropic",
        _capture_provider(captured, text="ok", output_tokens=1),
    )

    llm_client.ask_model(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-3-5-sonnet-latest",
        role="pm",
        max_tokens=8,
    )
    assert captured["max_tokens"] == 8


def test_ask_model_completion_cap_raises_when_output_exceeds(
    monkeypatch, _stub_budget
):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )
    huge = "x" * 100_000
    monkeypatch.setattr(
        llm_client,
        "_ask_anthropic",
        _capture_provider({}, text=huge, output_tokens=99999),
    )

    with pytest.raises(BudgetExceededError) as exc:
        llm_client.ask_model(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3-5-sonnet-latest",
            role="pm",
        )
    assert exc.value.channel in ("completion_tokens", "total_tokens_ceiling")


def test_ask_model_no_role_skips_budget(monkeypatch, _stub_budget):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: False)
    monkeypatch.setattr(
        llm_client, "_use_anthropic_backend", lambda model, llm_route: True
    )
    captured: dict = {}
    monkeypatch.setattr(
        llm_client,
        "_ask_anthropic",
        _capture_provider(captured, text="ok", output_tokens=1),
    )

    llm_client.ask_model(
        messages=[{"role": "user", "content": "x" * 100_000}],
        model="claude-3-5-sonnet-latest",
    )
    assert "thinking" not in captured


def test_ask_model_litellm_caps_completion(monkeypatch, _stub_budget):
    monkeypatch.setattr(llm_client, "cache_enabled", lambda: False)
    monkeypatch.setattr(llm_client, "_litellm_enabled", lambda: True)
    captured: dict = {}
    monkeypatch.setattr(
        llm_client,
        "_ask_litellm",
        _capture_provider(captured, text="ok", output_tokens=1),
    )

    llm_client.ask_model(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-3-5-sonnet-latest",
        role="pm",
    )
    assert captured["max_tokens"] == 64


def test_apply_reasoning_channel_openai_maps_to_effort():
    budget_low = RoleBudget(reasoning_tokens_max=512)
    budget_med = RoleBudget(reasoning_tokens_max=2048)
    budget_hi = RoleBudget(reasoning_tokens_max=8192)

    kw1 = role_budget_enforcer.apply_reasoning_channel(
        {}, budget_low, provider="openai", model="gpt", role="pm"
    )
    kw2 = role_budget_enforcer.apply_reasoning_channel(
        {}, budget_med, provider="openai", model="gpt", role="pm"
    )
    kw3 = role_budget_enforcer.apply_reasoning_channel(
        {}, budget_hi, provider="openai", model="gpt", role="pm"
    )
    assert kw1["reasoning_effort"] == "low"
    assert kw2["reasoning_effort"] == "medium"
    assert kw3["reasoning_effort"] == "high"


def test_apply_reasoning_channel_ollama_logs_once(caplog):
    role_budget_enforcer.reset_ollama_notice_cache()
    budget = RoleBudget(reasoning_tokens_max=1024)
    with caplog.at_level("INFO"):
        role_budget_enforcer.apply_reasoning_channel(
            {}, budget, provider="ollama", model="qwen", role="pm"
        )
        role_budget_enforcer.apply_reasoning_channel(
            {}, budget, provider="ollama", model="qwen", role="pm"
        )
    occurrences = [
        r for r in caplog.records if "no native reasoning channel" in r.getMessage()
    ]
    assert len(occurrences) == 1


def test_apply_completion_cap_respects_existing_smaller_value():
    budget = RoleBudget(completion_tokens_max=100)
    kw = role_budget_enforcer.apply_completion_cap({"max_tokens": 50}, budget)
    assert kw["max_tokens"] == 50


def test_apply_completion_cap_lowers_existing_larger_value():
    budget = RoleBudget(completion_tokens_max=100)
    kw = role_budget_enforcer.apply_completion_cap({"max_tokens": 500}, budget)
    assert kw["max_tokens"] == 100


def test_verify_total_budget_raises_on_ceiling_breach():
    budget = RoleBudget(
        completion_tokens_max=1000,
        total_tokens_ceiling=50,
        reasoning_tokens_max=0,
    )
    with pytest.raises(BudgetExceededError) as exc:
        role_budget_enforcer.verify_total_budget(
            prompt_tokens=40,
            output_text="x" * 100,
            budget=budget,
            role="pm",
            model="m",
        )
    assert exc.value.channel == "total_tokens_ceiling"


def test_enforce_prompt_budget_passes_under_cap():
    budget = RoleBudget(prompt_tokens_max=10_000, total_tokens_ceiling=100_000)
    used = role_budget_enforcer.enforce_prompt_budget(
        [{"role": "user", "content": "tiny"}], budget, role="pm", model="m"
    )
    assert used >= 1
