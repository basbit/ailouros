from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

VALID_CHECK_TYPES = frozenset({
    "artifact_count",
    "artifact_min_size",
    "agent_output_contains",
    "agent_output_min_chars",
    "no_warnings",
    "pipeline_step_count",
    "every_artifact_min_size",
    "agent_output_forbidden",
    "min_source_count",
    "claims_have_sources",
    "no_unverified_claims",
    "calculations_have_formula",
    "charts_minimum",
})

VALID_SEVERITIES = frozenset({"error", "warning", "info"})


@dataclass(frozen=True)
class QualityCheckSpec:
    id: str
    type: str
    severity: str = "error"
    blocking: bool = False
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "severity": self.severity,
            "blocking": self.blocking,
            "config": dict(self.config),
        }


@dataclass(frozen=True)
class QualityCheckResult:
    id: str
    type: str
    passed: bool
    severity: str
    blocking: bool
    message: str
    detail: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if self.detail is None:
            out.pop("detail", None)
        return out
