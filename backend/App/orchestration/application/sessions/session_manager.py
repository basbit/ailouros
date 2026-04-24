
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from backend.App.orchestration.domain.ports import SessionStorePort
from backend.App.orchestration.domain.session import AgentSession, SessionCheckpoint, SessionStatus


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class SessionManager:

    def __init__(self, store: SessionStorePort) -> None:
        self._store = store

    def create_session(self, task_id: str, metadata: dict[str, Any] | None = None) -> AgentSession:
        now = _now_iso()
        session = AgentSession(
            session_id=str(uuid.uuid4()),
            task_id=task_id,
            status=SessionStatus.PENDING,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._store.save_session(session)
        return session

    def checkpoint(
        self,
        session_id: str,
        step_name: str,
        state: dict[str, Any],
    ) -> SessionCheckpoint:
        cp = SessionCheckpoint(
            checkpoint_id=str(uuid.uuid4()),
            session_id=session_id,
            step_name=step_name,
            state_snapshot=state,
            created_at=_now_iso(),
        )
        self._store.save_checkpoint(cp)
        session = self._store.get_session(session_id)
        if session:
            session.last_checkpoint_id = cp.checkpoint_id
            session.updated_at = _now_iso()
            session.status = SessionStatus.RUNNING
            self._store.save_session(session)
        return cp

    def resume_session(
        self,
        session_id: str,
    ) -> tuple[AgentSession, SessionCheckpoint | None] | None:
        session = self._store.get_session(session_id)
        if session is None:
            return None
        session.status = SessionStatus.RESUMING
        session.updated_at = _now_iso()
        self._store.save_session(session)
        cp = self._store.get_latest_checkpoint(session_id)
        return session, cp

    def complete_session(self, session_id: str) -> None:
        self._update_status(session_id, SessionStatus.COMPLETED)

    def fail_session(self, session_id: str, reason: str = "") -> None:
        session = self._store.get_session(session_id)
        if session:
            session.status = SessionStatus.FAILED
            session.updated_at = _now_iso()
            if reason:
                session.metadata["failure_reason"] = reason
            self._store.save_session(session)

    def _update_status(self, session_id: str, status: SessionStatus) -> None:
        session = self._store.get_session(session_id)
        if session:
            session.status = status
            session.updated_at = _now_iso()
            self._store.save_session(session)
