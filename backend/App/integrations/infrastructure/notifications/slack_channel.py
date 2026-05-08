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


class SlackNotificationChannel(NotificationChannelPort):
    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_seconds: float = 4.0,
        channel_name: str = "slack",
    ) -> None:
        if not webhook_url or not webhook_url.lower().startswith("https://"):
            raise ValueError(
                f"SlackNotificationChannel.webhook_url={webhook_url!r} must be https URL"
            )
        self._webhook_url = webhook_url
        self._timeout = max(0.5, float(timeout_seconds))
        self._channel_name = channel_name
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._channel_name

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        body = json.dumps(
            self._format_payload(event),
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._webhook_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        sent_at = time.time()
        with self._lock:
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
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

    def _format_payload(self, event: NotificationEvent) -> dict[str, object]:
        severity_tag = f"[{event.severity.upper()}]"
        text = f"{severity_tag} {event.title}\n{event.summary}"
        fields: list[dict[str, object]] = []
        if event.task_id:
            fields.append({"title": "task", "value": event.task_id, "short": True})
        if event.scenario_id:
            fields.append(
                {"title": "scenario", "value": event.scenario_id, "short": True},
            )
        if event.project:
            fields.append({"title": "project", "value": event.project, "short": True})
        if event.artifact_path:
            fields.append(
                {"title": "artifact", "value": event.artifact_path, "short": False},
            )
        return {
            "text": text,
            "attachments": [
                {
                    "color": _slack_color(event.severity),
                    "fields": fields,
                }
            ] if fields else [],
        }


def _slack_color(severity: str) -> str:
    if severity == "critical":
        return "#a30000"
    if severity == "error":
        return "#d7563f"
    if severity == "warning":
        return "#f5b740"
    return "#3b5bdb"


__all__ = ("SlackNotificationChannel",)
