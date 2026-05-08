from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


NotificationKind = str
NotificationSeverity = str

VALID_KINDS: frozenset[NotificationKind] = frozenset({
    "human_gate_reached",
    "run_completed",
    "run_failed",
    "run_blocked",
    "risk_detected",
    "long_run_digest",
})

VALID_SEVERITIES: frozenset[NotificationSeverity] = frozenset({
    "info",
    "warning",
    "error",
    "critical",
})


@dataclass(frozen=True)
class NotificationEvent:
    kind: NotificationKind
    severity: NotificationSeverity
    title: str
    summary: str
    task_id: str | None = None
    project: str | None = None
    scenario_id: str | None = None
    artifact_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(
                f"NotificationEvent.kind={self.kind!r} is not a recognised kind"
            )
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"NotificationEvent.severity={self.severity!r} is not a recognised severity"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NotificationDelivery:
    channel: str
    kind: NotificationKind
    severity: NotificationSeverity
    accepted: bool
    detail: str
    sent_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotificationChannelPort(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        ...


class NotificationLedgerPort(ABC):
    @abstractmethod
    def record(self, delivery: NotificationDelivery) -> None:
        ...

    @abstractmethod
    def list_recent(self, limit: int = 100) -> list[NotificationDelivery]:
        ...


__all__ = (
    "VALID_KINDS",
    "VALID_SEVERITIES",
    "NotificationEvent",
    "NotificationDelivery",
    "NotificationChannelPort",
    "NotificationLedgerPort",
)
