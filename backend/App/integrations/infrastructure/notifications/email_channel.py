from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

from backend.App.integrations.domain.notifications import (
    NotificationChannelPort,
    NotificationDelivery,
    NotificationEvent,
)

logger = logging.getLogger(__name__)


class EmailNotificationChannel(NotificationChannelPort):
    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        sender_address: str,
        recipient_addresses: list[str],
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout_seconds: float = 6.0,
        channel_name: str = "email",
    ) -> None:
        if not smtp_host:
            raise ValueError("EmailNotificationChannel.smtp_host is required")
        if smtp_port < 1 or smtp_port > 65535:
            raise ValueError(
                f"EmailNotificationChannel.smtp_port={smtp_port!r} must be 1..65535"
            )
        if not sender_address:
            raise ValueError("EmailNotificationChannel.sender_address is required")
        cleaned_recipients = [
            address.strip()
            for address in recipient_addresses or []
            if address and address.strip()
        ]
        if not cleaned_recipients:
            raise ValueError(
                "EmailNotificationChannel requires at least one recipient address"
            )
        self._smtp_host = smtp_host
        self._smtp_port = int(smtp_port)
        self._sender = sender_address
        self._recipients = cleaned_recipients
        self._username = username
        self._password = password
        self._use_tls = bool(use_tls)
        self._timeout = max(1.0, float(timeout_seconds))
        self._channel_name = channel_name
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._channel_name

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = ", ".join(self._recipients)
        message["Subject"] = f"[{event.severity.upper()}] {event.title}"[:160]
        message.set_content(self._render_body(event))
        sent_at = time.time()
        with self._lock:
            try:
                if self._use_tls:
                    context = ssl.create_default_context()
                    with smtplib.SMTP(
                        self._smtp_host, self._smtp_port, timeout=self._timeout,
                    ) as server:
                        server.starttls(context=context)
                        if self._username and self._password:
                            server.login(self._username, self._password)
                        server.send_message(message)
                else:
                    with smtplib.SMTP(
                        self._smtp_host, self._smtp_port, timeout=self._timeout,
                    ) as server:
                        if self._username and self._password:
                            server.login(self._username, self._password)
                        server.send_message(message)
                return NotificationDelivery(
                    channel=self._channel_name,
                    kind=event.kind,
                    severity=event.severity,
                    accepted=True,
                    detail="smtp_ok",
                    sent_at=sent_at,
                )
            except (smtplib.SMTPException, OSError, ssl.SSLError) as transport_error:
                return NotificationDelivery(
                    channel=self._channel_name,
                    kind=event.kind,
                    severity=event.severity,
                    accepted=False,
                    detail=f"smtp_error:{transport_error}",
                    sent_at=sent_at,
                )

    def _render_body(self, event: NotificationEvent) -> str:
        lines: list[str] = [
            event.summary,
            "",
            f"task_id: {event.task_id or '-'}",
            f"project: {event.project or '-'}",
            f"scenario_id: {event.scenario_id or '-'}",
            f"artifact_path: {event.artifact_path or '-'}",
        ]
        for key, value in (event.extra or {}).items():
            lines.append(f"{key}: {value}")
        return "\n".join(lines)


__all__ = ("EmailNotificationChannel",)
