from __future__ import annotations

import pytest

from backend.App.integrations.domain.role_budgets import (
    BudgetExceededError,
    RoleBudget,
    parse_role_budgets,
)


_ALL_ROLES = [
    "pm",
    "ba",
    "architect",
    "dev_lead",
    "dev",
    "qa",
    "review_dev",
    "review_pm",
    "review_ba",
    "human_qa",
    "spec_drafter",
    "codegen_agent",
    "code_verifier",
]


def _full_raw(overrides=None):
    overrides = overrides or {}
    base = {
        role: {
            "prompt_tokens_max": 4096,
            "reasoning_tokens_max": 1024,
            "completion_tokens_max": 2048,
            "total_tokens_ceiling": 8192,
        }
        for role in _ALL_ROLES
    }
    base.update(overrides)
    return base


def test_parse_role_budgets_happy_path_returns_all_roles():
    parsed = parse_role_budgets(_full_raw())
    assert set(parsed.keys()) == set(_ALL_ROLES)
    assert isinstance(parsed["pm"], RoleBudget)
    assert parsed["pm"].prompt_tokens_max == 4096
    assert parsed["pm"].reasoning_tokens_max == 1024
    assert parsed["pm"].completion_tokens_max == 2048
    assert parsed["pm"].total_tokens_ceiling == 8192


def test_parse_role_budgets_allows_optional_none_fields():
    raw = _full_raw({"pm": {"prompt_tokens_max": 4096}})
    parsed = parse_role_budgets(raw)
    assert parsed["pm"].prompt_tokens_max == 4096
    assert parsed["pm"].reasoning_tokens_max is None
    assert parsed["pm"].completion_tokens_max is None
    assert parsed["pm"].total_tokens_ceiling is None


def test_parse_role_budgets_rejects_non_int_value():
    raw = _full_raw({"pm": {"prompt_tokens_max": "4096"}})
    with pytest.raises(ValueError, match="prompt_tokens_max"):
        parse_role_budgets(raw)


def test_parse_role_budgets_rejects_negative_value():
    raw = _full_raw({"pm": {"prompt_tokens_max": -1}})
    with pytest.raises(ValueError, match="non-negative"):
        parse_role_budgets(raw)


def test_parse_role_budgets_rejects_bool_value():
    raw = _full_raw({"pm": {"prompt_tokens_max": True}})
    with pytest.raises(ValueError, match="expected int"):
        parse_role_budgets(raw)


def test_parse_role_budgets_rejects_unknown_field():
    raw = _full_raw({"pm": {"prompt_tokens_max": 1, "rogue_field": 2}})
    with pytest.raises(ValueError, match="unknown field"):
        parse_role_budgets(raw)


def test_parse_role_budgets_rejects_missing_known_role():
    raw = _full_raw()
    raw.pop("architect")
    with pytest.raises(ValueError, match="missing required role"):
        parse_role_budgets(raw)


def test_parse_role_budgets_rejects_non_object_root():
    with pytest.raises(ValueError, match="root must be a JSON object"):
        parse_role_budgets([])


def test_parse_role_budgets_rejects_non_object_role_value():
    raw = _full_raw({"pm": "not-an-object"})
    with pytest.raises(ValueError, match="expected JSON object"):
        parse_role_budgets(raw)


def test_budget_exceeded_error_includes_channel_and_role():
    err = BudgetExceededError(channel="prompt_tokens", used=1000, cap=500, role="pm")
    assert err.channel == "prompt_tokens"
    assert err.used == 1000
    assert err.cap == 500
    assert err.role == "pm"
    assert "prompt_tokens" in str(err)
    assert "pm" in str(err)


def test_role_budget_is_frozen():
    rb = RoleBudget(prompt_tokens_max=1)
    with pytest.raises(Exception):
        rb.prompt_tokens_max = 2
