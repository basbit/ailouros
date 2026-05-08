from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from backend.App.integrations.domain.notifications import (
    NotificationChannelPort,
    NotificationDelivery,
    NotificationEvent,
)

logger = logging.getLogger(__name__)


class WebhookNotificationChannel(NotificationChannelPort):
    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str | None = None,
        timeout_seconds: float = 4.0,
        channel_name: str = "webhook",
    ) -> None:
        if not endpoint or not endpoint.lower().startswith(("http://", "https://")):
            raise ValueError(
                f"WebhookNotificationChannel.endpoint={endpoint!r} must be an http(s) URL"
            )
        self._endpoint = endpoint
        self._bearer_token = (bearer_token or "").strip()
        self._timeout_seconds = max(0.5, float(timeout_seconds))
        self._channel_name = channel_name
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._channel_name

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        payload = {
            "kind": event.kind,
            "severity": event.severity,
            "title": event.title,
            "summary": event.summary,
            "task_id": event.task_id,
            "project": event.project,
            "scenario_id": event.scenario_id,
            "artifact_path": event.artifact_path,
            "extra": event.extra or {},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self._bearer_token:
            request.add_header("Authorization", f"Bearer {self._bearer_token}")
        with self._lock:
            sent_at = time.time()
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    status_code = int(getattr(response, "status", 0) or 0)
                    accepted = 200 <= status_code < 300
                    return NotificationDelivery(
                        channel=self._channel_name,
                        kind=event.kind,
                        severity=event.severity,
                        accepted=accepted,
                        detail=f"http_{status_code}",
                        sent_at=sent_at,
                    )
            except urllib.error.HTTPError as http_error:
                return NotificationDelivery(
                    channel=self._channel_name,
                    kind=event.kind,
                    severity=event.severity,
                    accepted=False,
                    detail=f"http_{http_error.code}",
                    sent_at=sent_at,
                )
            except (urllib.error.URLError, TimeoutError) as transport_error:
                return NotificationDelivery(
                    channel=self._channel_name,
                    kind=event.kind,
                    severity=event.severity,
                    accepted=False,
                    detail=f"transport_error:{transport_error}",
                    sent_at=sent_at,
                )


__all__ = ("WebhookNotificationChannel",)
