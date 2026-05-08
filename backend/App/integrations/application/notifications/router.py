from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from backend.App.integrations.application.notifications.policy import (
    NotificationPolicy,
)
from backend.App.integrations.application.notifications.redaction import (
    redact_event_payload,
)
from backend.App.integrations.domain.notifications import (
    NotificationChannelPort,
    NotificationDelivery,
    NotificationEvent,
    NotificationLedgerPort,
)

logger = logging.getLogger(__name__)


class NotificationRouter:
    def __init__(
        self,
        *,
        policy: NotificationPolicy,
        channels: Iterable[NotificationChannelPort],
        ledger: NotificationLedgerPort | None = None,
    ) -> None:
        self._policy = policy
        self._channels: list[NotificationChannelPort] = list(channels)
        self._ledger = ledger
        self._lock = threading.Lock()

    def dispatch(self, event: NotificationEvent) -> list[NotificationDelivery]:
        decision = self._policy.evaluate(event)
        if not decision.accepted:
            logger.info(
                "notification.skipped kind=%s severity=%s reason=%s",
                event.kind, event.severity, decision.reason,
            )
            return []
        safe_event = NotificationEvent(
            kind=event.kind,
            severity=event.severity,
            title=event.title,
            summary=event.summary,
            task_id=event.task_id,
            project=event.project,
            scenario_id=event.scenario_id,
            artifact_path=event.artifact_path,
            extra=redact_event_payload(event.extra or {}),
        )
        deliveries: list[NotificationDelivery] = []
        with self._lock:
            channels = list(self._channels)
        for channel in channels:
            try:
                delivery = channel.deliver(safe_event)
            except Exception as caught:
                logger.warning(
                    "notification.channel_error channel=%s kind=%s detail=%s",
                    channel.name, safe_event.kind, caught,
                )
                delivery = NotificationDelivery(
                    channel=channel.name,
                    kind=safe_event.kind,
                    severity=safe_event.severity,
                    accepted=False,
                    detail=f"channel_error: {caught}",
                    sent_at=time.time(),
                )
            deliveries.append(delivery)
            if self._ledger is not None:
                try:
                    self._ledger.record(delivery)
                except Exception as caught:
                    logger.warning(
                        "notification.ledger_error channel=%s kind=%s detail=%s",
                        channel.name, safe_event.kind, caught,
                    )
        return deliveries

    def add_channel(self, channel: NotificationChannelPort) -> None:
        with self._lock:
            if channel not in self._channels:
                self._channels.append(channel)


__all__ = ("NotificationRouter",)
