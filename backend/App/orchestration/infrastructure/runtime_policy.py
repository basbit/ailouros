"""Infrastructure loaders for runtime policy/config used by orchestration.

Keeps environment access outside the domain layer.
"""

from __future__ import annotations

import os
from functools import lru_cache

from backend.App.orchestration.domain.approval_policy import (
    ApprovalPolicy,
    ApprovalPolicyArtifact,
    ApprovalPolicyConfig,
    ApprovalRule,
    ApprovalRuleSpec,
)
from backend.App.orchestration.domain.contract_validator import (
    ContractValidator,
    ContractValidatorLimits,
)
from backend.App.orchestration.domain.ports import RoleRegistry


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def load_approval_policy_from_env() -> ApprovalPolicy:
    """Build the runtime approval policy from environment variables."""
    low_risk_types = tuple(
        item.strip()
        for item in os.getenv(
            "SWARM_LOW_RISK_STEP_TYPES",
            "formatting,docs,test_generation",
        ).split(",")
        if item.strip()
    )
    artifact = ApprovalPolicyArtifact(
        rules=(
            ApprovalRuleSpec(ApprovalRule.REVIEWER_CONSENSUS),
            ApprovalRuleSpec(ApprovalRule.HIGH_CONFIDENCE),
            ApprovalRuleSpec(ApprovalRule.LOW_RISK_TYPE),
        )
    )
    return ApprovalPolicy(
        ApprovalPolicyConfig(
            mode=os.getenv("SWARM_AUTO_APPROVE", "policy").strip() or "policy",
            timeout_seconds=_int_env("SWARM_AUTO_APPROVE_TIMEOUT_SECONDS", 3600),
            low_risk_step_types=frozenset(low_risk_types),
            consensus_threshold=_float_env("SWARM_CONSENSUS_THRESHOLD", 2 / 3),
            artifact=artifact,
        )
    )


def load_role_registry_from_env() -> RoleRegistry:
    """Build the runtime role registry from environment variables."""
    custom_roles = [
        role_id.strip().lower()
        for role_id in os.getenv("SWARM_CUSTOM_ROLES", "").split(",")
        if role_id.strip()
    ]
    return RoleRegistry(custom_roles=custom_roles)


@lru_cache(maxsize=1)
def get_runtime_validator() -> ContractValidator:
    """Return the singleton ContractValidator configured from environment."""
    limits = ContractValidatorLimits(
        max_messages_per_task=_int_env("SWARM_MAX_MESSAGES_PER_TASK", 500),
        max_parent_depth=_int_env("SWARM_MAX_PARENT_DEPTH", 50),
        max_parallel_tasks=_int_env("SWARM_MAX_PARALLEL_TASKS", 20),
    )
    return ContractValidator(
        limits=limits,
        evidence_version=os.getenv("GIT_COMMIT", "").strip(),
    )


def reset_runtime_validator() -> None:
    """Reset the cached runtime validator (used by tests)."""
    get_runtime_validator.cache_clear()
