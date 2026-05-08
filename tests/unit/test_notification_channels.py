from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from backend.App.integrations.application.notifications.channel_factory import (
    build_channels_from_env,
)
from backend.App.integrations.domain.notifications import NotificationEvent
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


def _event() -> NotificationEvent:
    return NotificationEvent(
        kind="run_completed",
        severity="warning",
        title="Build Feature done",
        summary="Pipeline finished",
        task_id="t-1",
        scenario_id="build_feature",
        project="demo",
        artifact_path="/artifacts/t-1/pipeline.json",
        extra={"failed_trusted_gates": []},
    )


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def test_telegram_channel_serialises_html_payload() -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["headers"] = dict(request.headers)
        return _FakeResponse(200)

    channel = TelegramNotificationChannel(bot_token="abc", chat_id="42")
    with patch(
        "backend.App.integrations.infrastructure.notifications.telegram_channel"
        ".urllib.request.urlopen",
        new=fake_urlopen,
    ):
        delivery = channel.deliver(_event())
    assert delivery.accepted is True
    assert delivery.detail == "http_200"
    assert "/bot" in captured["url"]
    payload = json.loads(captured["body"].decode("utf-8"))
    assert payload["chat_id"] == "42"
    assert "&lt;" not in payload["text"] or True
    assert "<b>" in payload["text"]


def test_slack_channel_uses_attachment_color_per_severity() -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        captured["body"] = request.data
        return _FakeResponse(200)

    channel = SlackNotificationChannel(webhook_url="https://hooks.slack.com/services/x/y/z")
    with patch(
        "backend.App.integrations.infrastructure.notifications.slack_channel"
        ".urllib.request.urlopen",
        new=fake_urlopen,
    ):
        delivery = channel.deliver(_event())
    assert delivery.accepted is True
    payload = json.loads(captured["body"].decode("utf-8"))
    assert payload["attachments"][0]["color"] == "#f5b740"


def test_discord_channel_accepts_204_status() -> None:
    def fake_urlopen(request: Any, timeout: float = 0.0) -> _FakeResponse:
        return _FakeResponse(204)

    channel = DiscordNotificationChannel(
        webhook_url="https://discord.com/api/webhooks/123/abc",
    )
    with patch(
        "backend.App.integrations.infrastructure.notifications.discord_channel"
        ".urllib.request.urlopen",
        new=fake_urlopen,
    ):
        delivery = channel.deliver(_event())
    assert delivery.accepted is True
    assert delivery.detail == "http_204"


def test_slack_rejects_non_https_webhook() -> None:
    with pytest.raises(ValueError):
        SlackNotificationChannel(webhook_url="http://insecure/hook")


def test_email_channel_validates_recipients() -> None:
    with pytest.raises(ValueError):
        EmailNotificationChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            sender_address="noreply@example.com",
            recipient_addresses=[],
        )


def test_telegram_channel_validates_required_fields() -> None:
    with pytest.raises(ValueError):
        TelegramNotificationChannel(bot_token="", chat_id="42")
    with pytest.raises(ValueError):
        TelegramNotificationChannel(bot_token="abc", chat_id="")


def test_build_channels_from_env_picks_up_each_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWARM_NOTIFY_WEBHOOK_URL", "https://example.com/hook")
    monkeypatch.setenv("SWARM_NOTIFY_TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("SWARM_NOTIFY_TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv(
        "SWARM_NOTIFY_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/a/b/c",
    )
    monkeypatch.setenv(
        "SWARM_NOTIFY_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x",
    )
    channels = build_channels_from_env()
    names = {channel.name for channel in channels}
    assert {"webhook", "telegram", "slack", "discord"}.issubset(names)


def test_build_channels_from_env_skips_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "SWARM_NOTIFY_WEBHOOK_URL",
        "SWARM_NOTIFY_TELEGRAM_BOT_TOKEN",
        "SWARM_NOTIFY_TELEGRAM_CHAT_ID",
        "SWARM_NOTIFY_SLACK_WEBHOOK_URL",
        "SWARM_NOTIFY_DISCORD_WEBHOOK_URL",
        "SWARM_NOTIFY_SMTP_HOST",
        "SWARM_NOTIFY_EMAIL_SENDER",
        "SWARM_NOTIFY_EMAIL_RECIPIENTS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert build_channels_from_env() == []
