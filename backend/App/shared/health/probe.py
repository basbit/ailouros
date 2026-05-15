from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

ProbeStatus = Literal["ok", "degraded", "error", "disabled"]

PROBE_STATUS_VALUES: tuple[ProbeStatus, ...] = ("ok", "degraded", "error", "disabled")


@dataclass(frozen=True)
class ProbeResult:
    subsystem: str
    status: ProbeStatus
    latency_ms: float
    detail: str
    metadata: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "subsystem": self.subsystem,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 3),
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class HealthProbe(Protocol):
    subsystem: str

    def probe(self) -> ProbeResult:
        ...


__all__ = [
    "PROBE_STATUS_VALUES",
    "HealthProbe",
    "ProbeResult",
    "ProbeStatus",
]
