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


class TelegramNotificationChannel(NotificationChannelPort):
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 4.0,
        channel_name: str = "telegram",
    ) -> None:
        if not bot_token:
            raise ValueError("TelegramNotificationChannel.bot_token is required")
        if not chat_id:
            raise ValueError("TelegramNotificationChannel.chat_id is required")
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout = max(0.5, float(timeout_seconds))
        self._channel_name = channel_name
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._channel_name

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        text = self._format_text(event)
        endpoint = f"{self._api_base_url}/bot{self._bot_token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": self._chat_id,
                "text": text,
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
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

    def _format_text(self, event: NotificationEvent) -> str:
        title_safe = _escape_html(event.title)
        summary_safe = _escape_html(event.summary)
        severity_tag = f"[{event.severity.upper()}]"
        lines: list[str] = [
            f"<b>{severity_tag} {title_safe}</b>",
            summary_safe,
        ]
        if event.task_id:
            lines.append(f"task: <code>{_escape_html(event.task_id)}</code>")
        if event.scenario_id:
            lines.append(f"scenario: <code>{_escape_html(event.scenario_id)}</code>")
        if event.project:
            lines.append(f"project: <code>{_escape_html(event.project)}</code>")
        return "\n".join(lines)[:4000]


def _escape_html(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


__all__ = ("TelegramNotificationChannel",)
