"""Redis-backed SessionStore — production implementation (R1.1)."""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

from backend.App.orchestration.domain.ports import SessionStorePort
from backend.App.orchestration.domain.session import AgentSession, SessionCheckpoint, SessionStatus

logger = logging.getLogger(__name__)

_SESSION_TTL = 604_800   # 7 days
_CHECKPOINT_TTL = 86_400  # 24 h


class RedisSessionStore(SessionStorePort):
    def __init__(self, client: Any) -> None:
        self._client: Any = client

    # ------------------------------------------------------------------
    def save_session(self, session: AgentSession) -> None:
        try:
            key = f"session:{session.session_id}"
            payload = json.dumps(dataclasses.asdict(session))
            self._client.set(key, payload, ex=_SESSION_TTL)
            # Index: task_id → session_ids (sorted set scored by timestamp)
            self._client.sadd(f"sessions:task:{session.task_id}", session.session_id)
            self._client.expire(f"sessions:task:{session.task_id}", _SESSION_TTL)
        except Exception as exc:
            logger.warning("RedisSessionStore.save_session failed: %s", exc)

    def get_session(self, session_id: str) -> AgentSession | None:
        try:
            raw = self._client.get(f"session:{session_id}")
            if not raw:
                return None
            d = json.loads(raw)
            d["status"] = SessionStatus(d["status"])
            return AgentSession(**d)
        except Exception as exc:
            logger.warning("RedisSessionStore.get_session failed: %s", exc)
            return None

    def save_checkpoint(self, checkpoint: SessionCheckpoint) -> None:
        try:
            key = f"checkpoint:{checkpoint.session_id}:latest"
            payload = json.dumps(dataclasses.asdict(checkpoint))
            self._client.set(key, payload, ex=_CHECKPOINT_TTL)
        except Exception as exc:
            logger.warning("RedisSessionStore.save_checkpoint failed: %s", exc)

    def get_latest_checkpoint(self, session_id: str) -> SessionCheckpoint | None:
        try:
            raw = self._client.get(f"checkpoint:{session_id}:latest")
            if not raw:
                return None
            return SessionCheckpoint(**json.loads(raw))
        except Exception as exc:
            logger.warning("RedisSessionStore.get_latest_checkpoint failed: %s", exc)
            return None

    def list_sessions(self, task_id: str) -> list[AgentSession]:
        try:
            ids = self._client.smembers(f"sessions:task:{task_id}") or set()
            result = []
            for sid in ids:
                s = self.get_session(sid.decode() if isinstance(sid, bytes) else sid)
                if s:
                    result.append(s)
            return result
        except Exception as exc:
            logger.warning("RedisSessionStore.list_sessions failed: %s", exc)
            return []
