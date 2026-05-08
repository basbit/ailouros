from __future__ import annotations

import logging
import os

from backend.App.integrations.domain.notifications import (
    NotificationChannelPort,
)
from backend.App.integrations.infrastructure.notifications.discord_channel import (
    DiscordNotificationChannel,
)
from backend.App.integrations.infrastructure.notifications.email_channel import (
    EmailNotificationChannel,
)
from backend.App.integrations.infrastructure.notifications.slack_channel import (
    SlackNotificationChannel,
)
from backend.App.integrations.infrastructure.notifications.telegram_channel import (
    TelegramNotificationChannel,
)
from backend.App.integrations.infrastructure.notifications.webhook_channel import (
    WebhookNotificationChannel,
)

logger = logging.getLogger(__name__)


def build_channels_from_env() -> list[NotificationChannelPort]:
    channels: list[NotificationChannelPort] = []
    _maybe_add_webhook(channels)
    _maybe_add_email(channels)
    _maybe_add_telegram(channels)
    _maybe_add_slack(channels)
    _maybe_add_discord(channels)
    return channels


def _maybe_add_webhook(channels: list[NotificationChannelPort]) -> None:
    endpoint = (os.getenv("SWARM_NOTIFY_WEBHOOK_URL") or "").strip()
    if not endpoint:
        return
    try:
        channels.append(
            WebhookNotificationChannel(
                endpoint=endpoint,
                bearer_token=(os.getenv("SWARM_NOTIFY_WEBHOOK_TOKEN") or "").strip(),
            )
        )
    except ValueError as exc:
        logger.warning("notification webhook disabled: %s", exc)


def _maybe_add_email(channels: list[NotificationChannelPort]) -> None:
    smtp_host = (os.getenv("SWARM_NOTIFY_SMTP_HOST") or "").strip()
    sender = (os.getenv("SWARM_NOTIFY_EMAIL_SENDER") or "").strip()
    recipients_raw = (os.getenv("SWARM_NOTIFY_EMAIL_RECIPIENTS") or "").strip()
    if not smtp_host or not sender or not recipients_raw:
        return
    port_raw = (os.getenv("SWARM_NOTIFY_SMTP_PORT") or "587").strip()
    try:
        smtp_port = int(port_raw)
    except ValueError:
        logger.warning("notification email disabled: bad SMTP port %r", port_raw)
        return
    use_tls_raw = (os.getenv("SWARM_NOTIFY_SMTP_TLS") or "1").strip().lower()
    use_tls = use_tls_raw in {"1", "true", "yes", "on"}
    recipients = [item.strip() for item in recipients_raw.split(",") if item.strip()]
    try:
        channels.append(
            EmailNotificationChannel(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                sender_address=sender,
                recipient_addresses=recipients,
                username=(os.getenv("SWARM_NOTIFY_SMTP_USER") or "").strip() or None,
                password=(os.getenv("SWARM_NOTIFY_SMTP_PASSWORD") or "") or None,
                use_tls=use_tls,
            )
        )
    except ValueError as exc:
        logger.warning("notification email disabled: %s", exc)


def _maybe_add_telegram(channels: list[NotificationChannelPort]) -> None:
    bot_token = (os.getenv("SWARM_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("SWARM_NOTIFY_TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token or not chat_id:
        return
    try:
        channels.append(
            TelegramNotificationChannel(
                bot_token=bot_token,
                chat_id=chat_id,
            )
        )
    except ValueError as exc:
        logger.warning("notification telegram disabled: %s", exc)


def _maybe_add_slack(channels: list[NotificationChannelPort]) -> None:
    webhook = (os.getenv("SWARM_NOTIFY_SLACK_WEBHOOK_URL") or "").strip()
    if not webhook:
        return
    try:
        channels.append(SlackNotificationChannel(webhook_url=webhook))
    except ValueError as exc:
        logger.warning("notification slack disabled: %s", exc)


def _maybe_add_discord(channels: list[NotificationChannelPort]) -> None:
    webhook = (os.getenv("SWARM_NOTIFY_DISCORD_WEBHOOK_URL") or "").strip()
    if not webhook:
        return
    try:
        channels.append(DiscordNotificationChannel(webhook_url=webhook))
    except ValueError as exc:
        logger.warning("notification discord disabled: %s", exc)


__all__ = ("build_channels_from_env",)
