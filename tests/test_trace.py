"""Tests for R1.4 — Session tracing domain + InMemory collector."""
from __future__ import annotations

from unittest.mock import MagicMock

from backend.App.orchestration.domain.trace import EventType, TraceEvent
from backend.App.orchestration.infrastructure.in_memory_trace_collector import InMemoryTraceCollector


def _event(session_id: str = "s1", task_id: str = "t1", step: str = "pm") -> TraceEvent:
    return TraceEvent(
        event_id="e1",
        trace_id="tr1",
        session_id=session_id,
        task_id=task_id,
        step=step,
        event_type=EventType.STEP_START,
        timestamp="2026-04-09T00:00:00+00:00",
        data={"key": "val"},
    )


class TestTraceEvent:
    def test_creation(self):
        ev = _event()
        assert ev.event_type == EventType.STEP_START
        assert ev.session_id == "s1"

    def test_event_type_values(self):
        assert EventType.RUN_START == "run_start"
        assert EventType.HUMAN_APPROVAL == "human_approval"


class TestInMemoryTraceCollector:
    def test_record_creates_session(self):
        c = InMemoryTraceCollector()
        c.record(_event())
        session = c.get_session("s1")
        assert session is not None
        assert session.session_id == "s1"
        assert len(session.events) == 1

    def test_multiple_events_same_session(self):
        c = InMemoryTraceCollector()
        c.record(_event(step="pm"))
        c.record(_event(step="arch"))
        session = c.get_session("s1")
        assert len(session.events) == 2

    def test_get_missing_session_returns_none(self):
        c = InMemoryTraceCollector()
        assert c.get_session("nonexistent") is None

    def test_multiple_sessions(self):
        c = InMemoryTraceCollector()
        c.record(_event(session_id="s1"))
        c.record(_event(session_id="s2"))
        assert c.get_session("s1") is not None
        assert c.get_session("s2") is not None


class TestRedisTraceCollector:
    def test_record_calls_rpush(self):
        from backend.App.orchestration.infrastructure.redis_trace_collector import RedisTraceCollector
        client = MagicMock()
        c = RedisTraceCollector(client)
        c.record(_event())
        client.rpush.assert_called_once()
        client.expire.assert_called_once()

    def test_record_redis_failure_does_not_raise(self):
        from backend.App.orchestration.infrastructure.redis_trace_collector import RedisTraceCollector
        client = MagicMock()
        client.rpush.side_effect = Exception("Redis down")
        c = RedisTraceCollector(client)
        c.record(_event())  # must not raise

    def test_get_session_returns_none_on_empty(self):
        from backend.App.orchestration.infrastructure.redis_trace_collector import RedisTraceCollector
        client = MagicMock()
        client.lrange.return_value = []
        c = RedisTraceCollector(client)
        assert c.get_session("s1") is None
