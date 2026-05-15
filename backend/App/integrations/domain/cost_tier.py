from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.App.shared.domain.exceptions import DomainError

CostTier = Literal["cheap", "mid", "flagship"]

_VALID_TIERS: frozenset[str] = frozenset({"cheap", "mid", "flagship"})


@dataclass(frozen=True)
class RoleTierPolicy:
    role: str
    allowed_tiers: tuple[CostTier, ...]
    preferred_tier: CostTier


@dataclass(frozen=True)
class TierAssignment:
    model: str
    tier: CostTier
    provider: str


class CostTierViolation(DomainError):
    def __init__(
        self,
        role: str,
        model: str,
        actual_tier: str,
        allowed_tiers: tuple[str, ...],
    ) -> None:
        self.role = role
        self.model = model
        self.actual_tier = actual_tier
        self.allowed_tiers = allowed_tiers
        allowed_str = ", ".join(f"'{t}'" for t in allowed_tiers)
        super().__init__(
            f"Cost-tier violation: role='{role}' model='{model}' is tier='{actual_tier}' "
            f"but role only allows [{allowed_str}]. "
            f"Remediation: pick a model from an allowed tier or update role_policies in cost_tiers.json."
        )


__all__ = [
    "CostTier",
    "RoleTierPolicy",
    "TierAssignment",
    "CostTierViolation",
]
