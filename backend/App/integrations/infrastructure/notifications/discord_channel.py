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


class DiscordNotificationChannel(NotificationChannelPort):
    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_seconds: float = 4.0,
        channel_name: str = "discord",
    ) -> None:
        if not webhook_url or not webhook_url.lower().startswith("https://"):
            raise ValueError(
                f"DiscordNotificationChannel.webhook_url={webhook_url!r} must be https URL"
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
                    accepted = 200 <= status_code < 300 or status_code == 204
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
        fields: list[dict[str, object]] = []
        if event.task_id:
            fields.append({"name": "task", "value": event.task_id, "inline": True})
        if event.scenario_id:
            fields.append({"name": "scenario", "value": event.scenario_id, "inline": True})
        if event.project:
            fields.append({"name": "project", "value": event.project, "inline": True})
        if event.artifact_path:
            fields.append({"name": "artifact", "value": event.artifact_path, "inline": False})
        embed: dict[str, object] = {
            "title": f"[{event.severity.upper()}] {event.title}"[:240],
            "description": event.summary[:2000],
            "color": _discord_color(event.severity),
        }
        if fields:
            embed["fields"] = fields
        return {"embeds": [embed]}


def _discord_color(severity: str) -> int:
    if severity == "critical":
        return 0xA30000
    if severity == "error":
        return 0xD7563F
    if severity == "warning":
        return 0xF5B740
    return 0x3B5BDB


__all__ = ("DiscordNotificationChannel",)
