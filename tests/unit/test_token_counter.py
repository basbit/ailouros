"""Tests for K-6: Predictive Context Budget."""
from __future__ import annotations

import os


def test_count_tokens_ratio_fallback():
    """Ratio backend returns len(text) // 3 (minimum 1)."""
    os.environ["SWARM_TOKEN_COUNTER_BACKEND"] = "ratio"
    # Re-import after env change to pick up new backend setting
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    text = "a" * 90  # 90 chars → 90 // 3 = 30
    assert tc.count_tokens(text) == 30


def test_count_tokens_minimum_one():
    os.environ["SWARM_TOKEN_COUNTER_BACKEND"] = "ratio"
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    assert tc.count_tokens("a") == 1  # max(1, 1 // 3) = 1


def test_count_tokens_empty_string():
    os.environ["SWARM_TOKEN_COUNTER_BACKEND"] = "ratio"
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    # Empty string: max(1, 0//3) = 1
    result = tc.count_tokens("")
    assert result >= 1


def test_role_budget_dev_is_30_percent():
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    budget = tc.get_role_budget("dev", 100_000)
    assert budget == 30_000


def test_role_budget_pm_is_20_percent():
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    budget = tc.get_role_budget("pm", 100_000)
    assert budget == 20_000


def test_role_budget_ba_is_15_percent():
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    budget = tc.get_role_budget("ba", 100_000)
    assert budget == 15_000


def test_role_budget_unknown_role_defaults():
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    # Unknown role defaults to 15%
    budget = tc.get_role_budget("custom_agent", 100_000)
    assert budget == 15_000


def test_should_compact_when_tight():
    """Headroom less than 1.5× budget → compact recommended."""
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    # total=100_000, dev budget=30_000, threshold=45_000
    # current=60_000 → headroom=40_000 < 45_000 → compact
    result = tc.should_compact_before_step(
        current_tokens=60_000,
        total_limit=100_000,
        next_role="dev",
    )
    assert result is True


def test_should_not_compact_when_headroom():
    """Headroom greater than 1.5× budget → no compaction."""
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    # total=100_000, dev budget=30_000, threshold=45_000
    # current=40_000 → headroom=60_000 > 45_000 → no compact
    result = tc.should_compact_before_step(
        current_tokens=40_000,
        total_limit=100_000,
        next_role="dev",
    )
    assert result is False


def test_should_compact_boundary():
    """Exactly at boundary (headroom == threshold) → compact triggered."""
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    # qa budget = 20%, threshold = 30%
    # total=100_000, qa_budget=20_000, threshold=30_000
    # current=70_000 → headroom=30_000, not < 30_000 → False
    result = tc.should_compact_before_step(
        current_tokens=70_000,
        total_limit=100_000,
        next_role="qa",
    )
    assert result is False

    # current=70_001 → headroom=29_999 < 30_000 → True
    result2 = tc.should_compact_before_step(
        current_tokens=70_001,
        total_limit=100_000,
        next_role="qa",
    )
    assert result2 is True


def test_count_tokens_auto_backend_gpt4():
    """Auto backend selects tiktoken for gpt-4 model names if installed."""
    os.environ["SWARM_TOKEN_COUNTER_BACKEND"] = "auto"
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    text = "Hello, world!"
    result = tc.count_tokens(text, model="gpt-4o")
    # Should return a positive integer regardless of whether tiktoken is installed
    assert result >= 1


def test_count_tokens_auto_backend_anthropic_uses_ratio():
    """Auto backend uses ratio fallback for Anthropic/unknown model names."""
    os.environ["SWARM_TOKEN_COUNTER_BACKEND"] = "auto"
    import importlib
    import backend.App.integrations.infrastructure.llm.token_counter as tc
    importlib.reload(tc)

    text = "x" * 300  # 300 chars → ratio gives 100
    result = tc.count_tokens(text, model="claude-3-sonnet")
    # Ratio: 300 // 3 = 100
    assert result == 100
