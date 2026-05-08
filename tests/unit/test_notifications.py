from __future__ import annotations

import time
from typing import Any

import pytest

from backend.App.integrations.application.notifications.policy import (
    NotificationPolicy,
    PolicyDecision,
)
from backend.App.integrations.application.notifications.redaction import (
    redact_event_payload,
    redact_text,
)
from backend.App.integrations.application.notifications.router import (
    NotificationRouter,
)
from backend.App.integrations.domain.notifications import (
    NotificationChannelPort,
    NotificationDelivery,
    NotificationEvent,
)
from backend.App.integrations.infrastructure.notifications.in_memory_ledger import (
    InMemoryNotificationLedger,
)


class _FakeChannel(NotificationChannelPort):
    def __init__(self, name: str, *, accepted: bool = True) -> None:
        self._name = name
        self.accepted = accepted
        self.received: list[NotificationEvent] = []

    @property
    def name(self) -> str:
        return self._name

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        self.received.append(event)
        return NotificationDelivery(
            channel=self._name,
            kind=event.kind,
            severity=event.severity,
            accepted=self.accepted,
            detail="ok" if self.accepted else "rejected",
            sent_at=time.time(),
        )


class _ExplodingChannel(NotificationChannelPort):
    @property
    def name(self) -> str:
        return "exploding"

    def deliver(self, event: NotificationEvent) -> NotificationDelivery:
        raise RuntimeError("boom")


def _event(**overrides: Any) -> NotificationEvent:
    base = {
        "kind": "run_completed",
        "severity": "info",
        "title": "Build Feature done",
        "summary": "Pipeline completed for task t-1",
        "task_id": "t-1",
    }
    base.update(overrides)
    return NotificationEvent(**base)


def test_redact_text_strips_known_secrets() -> None:
    text = (
        "set api_key=sk-FAKE123456789ABCDEFGHIJKL and "
        "Bearer abcdefghijklmnopqrstuv stored at /Users/alice/.env"
    )
    redacted = redact_text(text)
    assert "[REDACTED-SECRET]" in redacted
    assert "[REDACTED-PATH]" in redacted
    assert "sk-FAKE" not in redacted


def test_redact_text_truncates_long_strings() -> None:
    long_text = "x" * 1000
    assert len(redact_text(long_text, max_chars=50)) == 50


def test_redact_event_payload_recurses() -> None:
    payload = {
        "outer": "AKIAABCDEFGHIJKLMNOP secret token",
        "nested": {
            "inner": "Bearer abcdefghijklmnopqrstuv",
            "list": ["nothing", "PATH=/Users/alice/.env"],
        },
    }
    redacted = redact_event_payload(payload)
    assert "[REDACTED-SECRET]" in redacted["outer"]
    assert "[REDACTED-SECRET]" in redacted["nested"]["inner"]
    assert "[REDACTED]" in redacted["nested"]["list"][1]
    assert redacted["nested"]["list"][0] == "nothing"


def test_event_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        NotificationEvent(
            kind="bogus",
            severity="info",
            title="x",
            summary="y",
        )


def test_event_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError):
        NotificationEvent(
            kind="run_completed",
            severity="loud",
            title="x",
            summary="y",
        )


def test_policy_blocks_below_severity_threshold() -> None:
    policy = NotificationPolicy(severity_threshold="error")
    decision = policy.evaluate(_event(severity="info"))
    assert isinstance(decision, PolicyDecision)
    assert decision.accepted is False
    assert "below threshold" in decision.reason


def test_policy_blocks_disabled_kind() -> None:
    policy = NotificationPolicy(enabled_kinds=frozenset({"run_failed"}))
    decision = policy.evaluate(_event(kind="run_completed"))
    assert decision.accepted is False
    assert "disabled" in decision.reason


def test_policy_rate_limit_blocks_after_threshold() -> None:
    policy = NotificationPolicy(rate_limit_per_minute=2)
    moment = time.time()
    assert policy.evaluate(_event(), when=moment).accepted is True
    assert policy.evaluate(_event(), when=moment).accepted is True
    third = policy.evaluate(_event(), when=moment)
    assert third.accepted is False
    assert "rate_limit" in third.reason


def test_policy_quiet_hours_block_within_window() -> None:
    current_hour = time.localtime().tm_hour
    start = current_hour
    end = (current_hour + 1) % 24
    policy = NotificationPolicy(quiet_hours=(start, end))
    decision = policy.evaluate(_event())
    assert decision.accepted is False
    assert "quiet_hours" in decision.reason


def test_router_dispatches_when_policy_accepts() -> None:
    channel = _FakeChannel("primary")
    router = NotificationRouter(
        policy=NotificationPolicy(),
        channels=[channel],
        ledger=InMemoryNotificationLedger(),
    )
    deliveries = router.dispatch(_event())
    assert len(deliveries) == 1
    assert deliveries[0].accepted is True
    assert channel.received and channel.received[0].task_id == "t-1"


def test_router_redacts_extra_payload_before_channel() -> None:
    channel = _FakeChannel("primary")
    router = NotificationRouter(
        policy=NotificationPolicy(),
        channels=[channel],
    )
    router.dispatch(
        _event(
            extra={"trace": "Bearer abcdefghijklmnopqrstuv inside the trace"},
        )
    )
    received_extra = channel.received[0].extra
    assert "[REDACTED-SECRET]" in received_extra["trace"]


def test_router_records_channel_errors_in_ledger() -> None:
    ledger = InMemoryNotificationLedger()
    router = NotificationRouter(
        policy=NotificationPolicy(),
        channels=[_ExplodingChannel()],
        ledger=ledger,
    )
    deliveries = router.dispatch(_event())
    assert len(deliveries) == 1
    assert deliveries[0].accepted is False
    assert "channel_error" in deliveries[0].detail
    recent = ledger.list_recent()
    assert len(recent) == 1
    assert recent[0].channel == "exploding"


def test_router_skips_when_policy_blocks() -> None:
    channel = _FakeChannel("primary")
    router = NotificationRouter(
        policy=NotificationPolicy(severity_threshold="critical"),
        channels=[channel],
    )
    deliveries = router.dispatch(_event(severity="info"))
    assert deliveries == []
    assert channel.received == []


def test_in_memory_ledger_capacity_evicts_oldest() -> None:
    ledger = InMemoryNotificationLedger(capacity=2)
    for index in range(4):
        ledger.record(
            NotificationDelivery(
                channel="primary",
                kind="run_completed",
                severity="info",
                accepted=True,
                detail=f"event-{index}",
                sent_at=time.time(),
            )
        )
    recent = ledger.list_recent()
    assert len(recent) == 2
    details = [item.detail for item in recent]
    assert "event-3" in details and "event-2" in details
