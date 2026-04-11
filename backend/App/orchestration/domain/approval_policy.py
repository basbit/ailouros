"""Auto-approval policies for human-gate pipeline steps (K-5).

Rules (INV-1, INV-6): every approval decision is logged as a structured event.
Disabled when SWARM_AUTO_APPROVE=0. Approves all when SWARM_AUTO_APPROVE=1.
Default: SWARM_AUTO_APPROVE=policy (rule-based evaluation).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

logger = logging.getLogger(__name__)


class ApprovalStep(TypedDict, total=False):
    step_id: str
    step_type: str


class ApprovalArtifactState(TypedDict, total=False):
    confidence_score: float


class ApprovalState(TypedDict, total=False):
    reviewer_verdicts: dict[str, list[str]]
    step_artifacts: dict[str, ApprovalArtifactState]


class ApprovalRule(str, Enum):
    REVIEWER_CONSENSUS = "reviewer_consensus"
    HIGH_CONFIDENCE = "high_confidence"
    LOW_RISK_TYPE = "low_risk_type"
    FORCED_APPROVE = "forced_approve"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    rule_matched: str | None
    reason: str


@dataclass(frozen=True)
class ApprovalPolicyConfig:
    mode: str = "policy"
    timeout_seconds: int = 3600
    low_risk_step_types: frozenset[str] = frozenset(
        {"formatting", "docs", "test_generation"}
    )
    consensus_threshold: float = 2 / 3
    artifact: "ApprovalPolicyArtifact | None" = None


@dataclass(frozen=True)
class ApprovalRuleSpec:
    rule: ApprovalRule
    enabled: bool = True


@dataclass(frozen=True)
class ApprovalPolicyArtifact:
    rules: tuple[ApprovalRuleSpec, ...] = field(
        default_factory=lambda: (
            ApprovalRuleSpec(ApprovalRule.REVIEWER_CONSENSUS),
            ApprovalRuleSpec(ApprovalRule.HIGH_CONFIDENCE),
            ApprovalRuleSpec(ApprovalRule.LOW_RISK_TYPE),
        )
    )


class ApprovalPolicy:
    """Evaluates whether a human-gate step should be auto-approved.

    Called before a step enters human wait state. If approved=True, the step
    is skipped automatically. Every decision is logged (INV-1, INV-6).

    Rules checked in order:
    1. reviewer_consensus: all reviewer verdicts OK, consensus >= 2/3
    2. high_confidence: artifact confidence_score > 0.9
    3. low_risk_type: step type in {formatting, docs, test_generation}
    """

    def __init__(self, config: ApprovalPolicyConfig | None = None) -> None:
        self._config = config or ApprovalPolicyConfig()
        self._artifact = self._config.artifact or ApprovalPolicyArtifact()

    def evaluate(self, step: ApprovalStep, state: ApprovalState) -> ApprovalDecision:
        decision = self._evaluate_internal(step, state)
        logger.info(  # INV-1, INV-6
            "AutoApproval: step=%s approved=%s rule=%s reason=%s",
            step.get("step_id", "unknown"),
            decision.approved,
            decision.rule_matched,
            decision.reason,
        )
        return decision

    def _evaluate_internal(self, step: ApprovalStep, state: ApprovalState) -> ApprovalDecision:
        mode = self._config.mode

        if mode == "0":
            return ApprovalDecision(
                approved=False,
                rule_matched=ApprovalRule.DISABLED,
                reason="SWARM_AUTO_APPROVE=0: all auto-approval disabled",
            )

        if mode == "1":
            return ApprovalDecision(
                approved=True,
                rule_matched=ApprovalRule.FORCED_APPROVE,
                reason="SWARM_AUTO_APPROVE=1: unconditional approval",
            )

        for rule_spec in self._artifact.rules:
            if not rule_spec.enabled:
                continue
            decision = self._evaluate_rule(rule_spec.rule, step, state)
            if decision is not None:
                return decision

        return ApprovalDecision(
            approved=False,
            rule_matched=None,
            reason="No auto-approval rule matched; step enters human wait state",
        )

    def _evaluate_rule(
        self,
        rule: ApprovalRule,
        step: ApprovalStep,
        state: ApprovalState,
    ) -> ApprovalDecision | None:
        if rule == ApprovalRule.REVIEWER_CONSENSUS and self._check_reviewer_consensus(step, state):
            return ApprovalDecision(
                approved=True,
                rule_matched=ApprovalRule.REVIEWER_CONSENSUS,
                reason="All reviewer verdicts OK with consensus >= 2/3",
            )
        if rule == ApprovalRule.HIGH_CONFIDENCE and self._check_high_confidence(step, state):
            return ApprovalDecision(
                approved=True,
                rule_matched=ApprovalRule.HIGH_CONFIDENCE,
                reason="Artifact confidence_score > 0.9",
            )
        if rule == ApprovalRule.LOW_RISK_TYPE and self._check_low_risk_type(step, state):
            return ApprovalDecision(
                approved=True,
                rule_matched=ApprovalRule.LOW_RISK_TYPE,
                reason=f"Step type '{step.get('step_type')}' is low-risk",
            )
        return None

    def _check_reviewer_consensus(self, step: ApprovalStep, state: ApprovalState) -> bool:
        verdicts: list[str] = state.get("reviewer_verdicts", {}).get(step.get("step_id", ""), [])
        if not verdicts:
            return False
        ok_count = sum(1 for v in verdicts if str(v).strip().upper() == "OK")
        return ok_count / len(verdicts) >= self._config.consensus_threshold

    def _check_high_confidence(self, step: ApprovalStep, state: ApprovalState) -> bool:
        artifacts = state.get("step_artifacts", {})
        artifact = artifacts.get(step.get("step_id", ""), {})
        score = artifact.get("confidence_score")
        try:
            return float(score) > 0.9
        except (TypeError, ValueError):
            return False

    def _check_low_risk_type(self, step: ApprovalStep, state: ApprovalState) -> bool:
        return step.get("step_type", "") in self._config.low_risk_step_types
