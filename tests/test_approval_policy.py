"""Tests for K-5: Auto-Approval Policies."""
from __future__ import annotations

from backend.App.orchestration.domain.approval_policy import (
    ApprovalPolicy,
    ApprovalPolicyArtifact,
    ApprovalPolicyConfig,
    ApprovalRuleSpec,
    ApprovalRule,
)


def _policy(
    *,
    mode: str = "policy",
    threshold: float = 2 / 3,
    low_risk: frozenset[str] | None = None,
) -> ApprovalPolicy:
    return ApprovalPolicy(
        ApprovalPolicyConfig(
            mode=mode,
            low_risk_step_types=low_risk or frozenset(
                {"formatting", "docs", "test_generation"}
            ),
            consensus_threshold=threshold,
        )
    )


def test_auto_approve_disabled():
    decision = _policy(mode="0").evaluate({"step_id": "pm"}, {})
    assert decision.approved is False
    assert decision.rule_matched == ApprovalRule.DISABLED


def test_auto_approve_forced():
    decision = _policy(mode="1").evaluate({"step_id": "pm"}, {})
    assert decision.approved is True
    assert decision.rule_matched == ApprovalRule.FORCED_APPROVE


def test_reviewer_consensus_all_ok():
    state = {"reviewer_verdicts": {"pm": ["OK", "OK", "OK"]}}
    decision = _policy().evaluate({"step_id": "pm"}, state)
    assert decision.approved is True
    assert decision.rule_matched == ApprovalRule.REVIEWER_CONSENSUS


def test_reviewer_consensus_partial_above_threshold():
    """2 out of 3 (66.7%) >= 2/3 threshold — should approve."""
    state = {"reviewer_verdicts": {"pm": ["OK", "OK", "NEEDS_WORK"]}}
    decision = _policy().evaluate({"step_id": "pm"}, state)
    assert decision.approved is True
    assert decision.rule_matched == ApprovalRule.REVIEWER_CONSENSUS


def test_reviewer_consensus_below_threshold():
    """1 out of 3 (33%) < 2/3 threshold — should not approve via this rule."""
    state = {
        "reviewer_verdicts": {"pm": ["OK", "NEEDS_WORK", "NEEDS_WORK"]},
        "step_artifacts": {"pm": {"confidence_score": 0.0}},
    }
    decision = _policy().evaluate({"step_id": "pm", "step_type": "other"}, state)
    assert decision.approved is False


def test_high_confidence():
    state = {"step_artifacts": {"pm": {"confidence_score": 0.95}}}
    decision = _policy().evaluate({"step_id": "pm"}, state)
    assert decision.approved is True
    assert decision.rule_matched == ApprovalRule.HIGH_CONFIDENCE


def test_low_confidence_falls_through():
    state = {"step_artifacts": {"pm": {"confidence_score": 0.5}}}
    decision = _policy().evaluate({"step_id": "pm", "step_type": "other"}, state)
    assert decision.approved is False


def test_low_risk_type_formatting():
    decision = _policy().evaluate({"step_id": "x", "step_type": "formatting"}, {})
    assert decision.approved is True
    assert decision.rule_matched == ApprovalRule.LOW_RISK_TYPE


def test_no_rule_matched():
    decision = _policy().evaluate({"step_id": "x", "step_type": "implementation"}, {})
    assert decision.approved is False
    assert decision.rule_matched is None


def test_evaluate_logs_decision(caplog):
    """Every evaluate() call produces a log entry (INV-1, INV-6)."""
    import logging
    with caplog.at_level(logging.INFO):
        _policy(mode="0").evaluate({"step_id": "pm"}, {})
    assert "pm" in caplog.text


def test_artifact_rule_order_is_respected() -> None:
    policy = ApprovalPolicy(
        ApprovalPolicyConfig(
            artifact=ApprovalPolicyArtifact(
                rules=(
                    ApprovalRuleSpec(ApprovalRule.LOW_RISK_TYPE),
                    ApprovalRuleSpec(ApprovalRule.HIGH_CONFIDENCE),
                )
            )
        )
    )
    decision = policy.evaluate(
        {"step_id": "pm", "step_type": "formatting"},
        {"step_artifacts": {"pm": {"confidence_score": 0.95}}},
    )
    assert decision.rule_matched == ApprovalRule.LOW_RISK_TYPE
