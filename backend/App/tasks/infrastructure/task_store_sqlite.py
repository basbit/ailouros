from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.application.datetime_utils import utc_now_iso

logger = logging.getLogger(__name__)

__all__ = ["SqliteTaskStore"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_updated_at_idx ON tasks(updated_at);
"""

DEFAULT_MAX_TASKS = 1000


class SqliteTaskStore:
    def __init__(
        self,
        db_path: Path,
        *,
        max_size: int = DEFAULT_MAX_TASKS,
    ) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._db_path = db_path
        self._max_size = max_size
        self._lock = threading.RLock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA)

    @staticmethod
    def _new_payload(prompt: str) -> dict[str, Any]:
        return {
            "task_id": str(uuid.uuid4()),
            "task": prompt,
            "status": "in_progress",
            "agents": [],
            "history": [],
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "version": 0,
        }

    def _save(self, payload: dict[str, Any]) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO tasks (task_id, payload, updated_at) VALUES (?, ?, ?)",
            (
                payload["task_id"],
                json.dumps(payload, ensure_ascii=False),
                payload["updated_at"],
            ),
        )
        self._evict_oldest()

    def _evict_oldest(self) -> None:
        cursor = self._connection.execute("SELECT COUNT(*) FROM tasks")
        (count,) = cursor.fetchone()
        if count <= self._max_size:
            return
        excess = count - self._max_size
        self._connection.execute(
            "DELETE FROM tasks WHERE task_id IN ("
            "SELECT task_id FROM tasks ORDER BY updated_at ASC LIMIT ?)",
            (excess,),
        )
        logger.debug("SqliteTaskStore evicted %d oldest entries", excess)

    @staticmethod
    def _apply_update(
        payload: dict[str, Any],
        *,
        status: Optional[str],
        agent: Optional[str],
        message: Optional[str],
        scenario_id: Optional[str] = None,
        scenario_title: Optional[str] = None,
        scenario_category: Optional[str] = None,
    ) -> dict[str, Any]:
        if status is not None:
            payload["status"] = status
        if agent is not None and str(agent).strip():
            agents = payload.setdefault("agents", [])
            if agent not in agents:
                agents.append(agent)
        if message is not None and str(message).strip():
            payload.setdefault("history", []).append(
                {
                    "timestamp": utc_now_iso(),
                    "agent": agent,
                    "message": message,
                }
            )
        if scenario_id is not None:
            payload["scenario_id"] = scenario_id
        if scenario_title is not None:
            payload["scenario_title"] = scenario_title
        if scenario_category is not None:
            payload["scenario_category"] = scenario_category
        payload["updated_at"] = utc_now_iso()
        payload["version"] = payload.get("version", 0) + 1
        return payload

    def create_task(self, prompt: str) -> dict[str, Any]:
        with self._lock:
            payload = self._new_payload(prompt)
            self._save(payload)
            return payload

    def get_task(self, task_id: Any) -> dict[str, Any]:
        task_id_str = str(task_id)
        with self._lock:
            cursor = self._connection.execute(
                "SELECT payload FROM tasks WHERE task_id = ?", (task_id_str,)
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(task_id_str)
            payload: dict[str, Any] = json.loads(row[0])
            return payload

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
        scenario_id: Optional[str] = None,
        scenario_title: Optional[str] = None,
        scenario_category: Optional[str] = None,
    ) -> dict[str, Any]:
        task_id_str = str(task_id)
        with self._lock:
            payload = self.get_task(task_id_str)
            payload = self._apply_update(
                payload,
                status=status,
                agent=agent,
                message=message,
                scenario_id=scenario_id,
                scenario_title=scenario_title,
                scenario_category=scenario_category,
            )
            self._save(payload)
            return payload

    def delete_task(self, task_id: Any) -> None:
        task_id_str = str(task_id)
        with self._lock:
            self._connection.execute(
                "DELETE FROM tasks WHERE task_id = ?", (task_id_str,)
            )

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(
                "SELECT payload FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (self._max_size,),
            )
            rows = cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def db_path(self) -> Path:
        return self._db_path
