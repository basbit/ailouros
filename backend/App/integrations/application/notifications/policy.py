from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from backend.App.integrations.domain.notifications import (
    NotificationEvent,
    VALID_KINDS,
    VALID_SEVERITIES,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PolicyDecision:
    accepted: bool
    reason: str


@dataclass
class NotificationPolicy:
    severity_threshold: str = "info"
    enabled_kinds: frozenset[str] = frozenset(VALID_KINDS)
    rate_limit_per_minute: int = 30
    quiet_hours: tuple[int, int] | None = None
    project_filters: dict[str, frozenset[str]] = field(default_factory=dict)
    _bucket: list[float] = field(default_factory=list)
    _bucket_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.severity_threshold not in VALID_SEVERITIES:
            raise ValueError(
                f"NotificationPolicy.severity_threshold={self.severity_threshold!r} is not recognised"
            )
        unknown = self.enabled_kinds - VALID_KINDS
        if unknown:
            raise ValueError(
                f"NotificationPolicy.enabled_kinds includes unknown values: {sorted(unknown)}"
            )
        if self.quiet_hours is not None:
            start, end = self.quiet_hours
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError(
                    f"NotificationPolicy.quiet_hours={self.quiet_hours!r} must be 0..23"
                )

    def _severity_rank(self, severity: str) -> int:
        order = ["info", "warning", "error", "critical"]
        try:
            return order.index(severity)
        except ValueError:
            return -1

    def _in_quiet_hours(self, when: float) -> bool:
        if self.quiet_hours is None:
            return False
        start, end = self.quiet_hours
        hour = time.localtime(when).tm_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _rate_allows(self, when: float) -> bool:
        with self._bucket_lock:
            cutoff = when - 60.0
            self._bucket[:] = [ts for ts in self._bucket if ts >= cutoff]
            if len(self._bucket) >= self.rate_limit_per_minute:
                return False
            self._bucket.append(when)
            return True

    def evaluate(self, event: NotificationEvent, *, when: float | None = None) -> PolicyDecision:
        moment = when if when is not None else time.time()
        if event.kind not in self.enabled_kinds:
            return PolicyDecision(False, f"kind={event.kind} is disabled")
        if self._severity_rank(event.severity) < self._severity_rank(self.severity_threshold):
            return PolicyDecision(
                False,
                f"severity={event.severity} below threshold={self.severity_threshold}",
            )
        if event.project and self.project_filters:
            allowed_kinds = self.project_filters.get(event.project)
            if allowed_kinds is not None and event.kind not in allowed_kinds:
                return PolicyDecision(
                    False,
                    f"project={event.project} does not allow kind={event.kind}",
                )
        if self._in_quiet_hours(moment):
            return PolicyDecision(
                False,
                f"quiet_hours={self.quiet_hours} active",
            )
        if not self._rate_allows(moment):
            return PolicyDecision(
                False,
                f"rate_limit_per_minute={self.rate_limit_per_minute} exceeded",
            )
        return PolicyDecision(True, "ok")


def default_policy() -> NotificationPolicy:
    return NotificationPolicy()


def policy_from_config(payload: dict[str, Any]) -> NotificationPolicy:
    severity = str(payload.get("severity_threshold") or "info").strip().lower()
    enabled_raw = payload.get("enabled_kinds")
    enabled_kinds = (
        frozenset(str(item).strip() for item in enabled_raw)
        if isinstance(enabled_raw, (list, tuple, set, frozenset))
        else frozenset(VALID_KINDS)
    )
    rate_limit_raw = payload.get("rate_limit_per_minute") or 30
    rate_limit = int(rate_limit_raw) if isinstance(rate_limit_raw, (int, float)) else 30
    quiet_raw = payload.get("quiet_hours")
    quiet_hours: tuple[int, int] | None = None
    if (
        isinstance(quiet_raw, (list, tuple))
        and len(quiet_raw) == 2
        and all(isinstance(value, (int, float)) for value in quiet_raw)
    ):
        quiet_hours = (int(quiet_raw[0]), int(quiet_raw[1]))
    project_filters_raw = payload.get("project_filters")
    project_filters: dict[str, frozenset[str]] = {}
    if isinstance(project_filters_raw, dict):
        for project_name, kinds in project_filters_raw.items():
            if not isinstance(kinds, (list, tuple, set)):
                continue
            project_filters[str(project_name)] = frozenset(
                str(value) for value in kinds
            )
    return NotificationPolicy(
        severity_threshold=severity,
        enabled_kinds=enabled_kinds,
        rate_limit_per_minute=max(1, rate_limit),
        quiet_hours=quiet_hours,
        project_filters=project_filters,
    )


__all__ = ("NotificationPolicy", "PolicyDecision", "default_policy", "policy_from_config")
