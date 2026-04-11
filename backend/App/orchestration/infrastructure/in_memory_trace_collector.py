"""In-memory TraceCollector — for dev/testing (R1.4)."""

from __future__ import annotations

from backend.App.orchestration.domain.ports import TraceCollectorPort
from backend.App.orchestration.domain.trace import TraceEvent, TraceSession


class InMemoryTraceCollector(TraceCollectorPort):
    def __init__(self) -> None:
        self._sessions: dict[str, TraceSession] = {}

    def record(self, event: TraceEvent) -> None:
        session = self._sessions.get(event.session_id)
        if session is None:
            session = TraceSession(
                session_id=event.session_id,
                task_id=event.task_id,
                run_id=event.trace_id,
                started_at=event.timestamp,
            )
            self._sessions[event.session_id] = session
        session.events.append(event)

    def get_session(self, session_id: str) -> TraceSession | None:
        return self._sessions.get(session_id)
