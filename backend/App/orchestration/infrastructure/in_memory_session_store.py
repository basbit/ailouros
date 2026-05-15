
from __future__ import annotations

import threading

from backend.App.orchestration.domain.ports import SessionStorePort
from backend.App.orchestration.domain.session import AgentSession, SessionCheckpoint


class InMemorySessionStore(SessionStorePort):
    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._checkpoints: dict[str, SessionCheckpoint] = {}
        self._lock = threading.Lock()

    def save_session(self, session: AgentSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def save_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        with self._lock:
            self._checkpoints[checkpoint.session_id] = checkpoint

    def get_latest_checkpoint(self, session_id: str) -> SessionCheckpoint | None:
        return self._checkpoints.get(session_id)

    def list_sessions(self, task_id: str) -> list[AgentSession]:
        return [s for s in self._sessions.values() if s.task_id == task_id]
