from __future__ import annotations

import pytest

from backend.App.integrations.domain.cost_tier import (
    CostTierViolation,
    RoleTierPolicy,
    TierAssignment,
)
from backend.App.shared.domain.exceptions import DomainError


def test_role_tier_policy_is_frozen() -> None:
    pol = RoleTierPolicy(role="dev", allowed_tiers=("mid", "flagship"), preferred_tier="mid")
    with pytest.raises((TypeError, AttributeError)):
        pol.role = "qa"  # type: ignore[misc]


def test_role_tier_policy_fields() -> None:
    pol = RoleTierPolicy(role="qa", allowed_tiers=("cheap", "mid"), preferred_tier="cheap")
    assert pol.role == "qa"
    assert pol.allowed_tiers == ("cheap", "mid")
    assert pol.preferred_tier == "cheap"


def test_tier_assignment_is_frozen() -> None:
    ta = TierAssignment(model="gpt-4o", tier="mid", provider="openai")
    with pytest.raises((TypeError, AttributeError)):
        ta.model = "other"  # type: ignore[misc]


def test_tier_assignment_fields() -> None:
    ta = TierAssignment(model="claude-opus-4-7", tier="flagship", provider="anthropic")
    assert ta.model == "claude-opus-4-7"
    assert ta.tier == "flagship"
    assert ta.provider == "anthropic"


def test_cost_tier_violation_is_domain_error() -> None:
    exc = CostTierViolation(
        role="code_verifier",
        model="claude-opus-4-7",
        actual_tier="flagship",
        allowed_tiers=("cheap",),
    )
    assert isinstance(exc, DomainError)


def test_cost_tier_violation_message_contains_role_and_model() -> None:
    exc = CostTierViolation(
        role="code_verifier",
        model="claude-opus-4-7",
        actual_tier="flagship",
        allowed_tiers=("cheap",),
    )
    msg = str(exc)
    assert "code_verifier" in msg
    assert "claude-opus-4-7" in msg
    assert "flagship" in msg
    assert "cheap" in msg


def test_cost_tier_violation_message_contains_remediation() -> None:
    exc = CostTierViolation(
        role="qa",
        model="claude-opus-4-7",
        actual_tier="flagship",
        allowed_tiers=("cheap", "mid"),
    )
    assert "Remediation" in str(exc)


def test_cost_tier_violation_attributes() -> None:
    exc = CostTierViolation(
        role="spec_reviewer",
        model="gpt-4o-mini",
        actual_tier="cheap",
        allowed_tiers=("flagship",),
    )
    assert exc.role == "spec_reviewer"
    assert exc.model == "gpt-4o-mini"
    assert exc.actual_tier == "cheap"
    assert exc.allowed_tiers == ("flagship",)


def test_role_tier_policy_equality() -> None:
    p1 = RoleTierPolicy(role="dev", allowed_tiers=("mid",), preferred_tier="mid")
    p2 = RoleTierPolicy(role="dev", allowed_tiers=("mid",), preferred_tier="mid")
    assert p1 == p2


def test_tier_assignment_equality() -> None:
    t1 = TierAssignment(model="gpt-4o", tier="mid", provider="openai")
    t2 = TierAssignment(model="gpt-4o", tier="mid", provider="openai")
    assert t1 == t2
