from __future__ import annotations

import threading

from backend.App.integrations.domain.notifications import (
    NotificationDelivery,
    NotificationLedgerPort,
)


class InMemoryNotificationLedger(NotificationLedgerPort):
    def __init__(self, *, capacity: int = 200) -> None:
        if capacity < 1:
            raise ValueError(
                f"InMemoryNotificationLedger.capacity={capacity!r} must be >= 1"
            )
        self._capacity = capacity
        self._items: list[NotificationDelivery] = []
        self._lock = threading.Lock()

    def record(self, delivery: NotificationDelivery) -> None:
        with self._lock:
            self._items.append(delivery)
            overflow = len(self._items) - self._capacity
            if overflow > 0:
                del self._items[:overflow]

    def list_recent(self, limit: int = 100) -> list[NotificationDelivery]:
        with self._lock:
            cropped = self._items[-max(1, limit):]
            return list(reversed(cropped))


__all__ = ("InMemoryNotificationLedger",)
