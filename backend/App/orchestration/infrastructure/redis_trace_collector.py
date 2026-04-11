"""Redis-backed TraceCollector — production implementation (R1.4)."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from backend.App.orchestration.domain.ports import TraceCollectorPort
from backend.App.orchestration.domain.trace import EventType, TraceEvent, TraceSession

logger = logging.getLogger(__name__)

_TTL_SECONDS = 86_400  # 24 h


class RedisTraceCollector(TraceCollectorPort):
    """Stores trace events as a Redis list ``trace:{session_id}``."""

    def __init__(self, client: Any) -> None:
        # ``client`` is a ``redis.Redis`` instance — kept as ``Any`` to
        # avoid importing redis at module load time (INV-7 friendly).
        self._client: Any = client

    # ------------------------------------------------------------------
    def record(self, event: TraceEvent) -> None:
        try:
            key = f"trace:{event.session_id}"
            payload = json.dumps(dataclasses.asdict(event))
            self._client.rpush(key, payload)
            self._client.expire(key, _TTL_SECONDS)
        except Exception as exc:
            logger.warning("RedisTraceCollector.record failed (skipping): %s", exc)

    def get_session(self, session_id: str) -> TraceSession | None:
        try:
            key = f"trace:{session_id}"
            raw_events = self._client.lrange(key, 0, -1)
            if not raw_events:
                return None
            events: list[TraceEvent] = []
            task_id = ""
            run_id = ""
            started_at = ""
            for raw in raw_events:
                d = json.loads(raw)
                d["event_type"] = EventType(d["event_type"])
                ev = TraceEvent(**d)
                events.append(ev)
                if not task_id:
                    task_id = ev.task_id
                    run_id = ev.trace_id
                    started_at = ev.timestamp
            return TraceSession(
                session_id=session_id,
                task_id=task_id,
                run_id=run_id,
                started_at=started_at,
                events=events,
            )
        except Exception as exc:
            logger.warning("RedisTraceCollector.get_session failed: %s", exc)
            return None
