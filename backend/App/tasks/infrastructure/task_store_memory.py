"""Pure in-memory task store with LRU eviction.

No Redis dependency. Implements the TaskStorePort interface
(duck-typing compatible with the legacy TaskStore API).
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["InMemoryTaskStore"]

# Task status constants — mirrors backend.App.domain.ports.TaskStatus enum values.
TASK_STATUS_IN_PROGRESS: str = "in_progress"
TASK_STATUS_COMPLETED: str = "completed"
TASK_STATUS_FAILED: str = "failed"
TASK_STATUS_CANCELLED: str = "cancelled"
TASK_STATUS_AWAITING_HUMAN: str = "awaiting_human"
TASK_STATUS_AWAITING_SHELL: str = "awaiting_shell"


class InMemoryTaskStore:
    """In-process task store backed by an OrderedDict with LRU eviction.

    Thread-safety note: individual dict operations in CPython are
    effectively atomic due to the GIL, but compound read-modify-write
    sequences are not.  For single-worker deployments this is sufficient.
    Use RedisTaskStore or FallbackTaskStore for multi-worker setups.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_size = max(1, max_size)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_update(
        self,
        payload: dict[str, Any],
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a mutated copy of *payload* with the supplied fields applied."""
        # Distinguish "not passed" from "empty string" — use `is not None` checks.
        if status is not None:
            payload["status"] = status
        if agent is not None and str(agent).strip():
            if agent not in payload["agents"]:
                payload["agents"].append(agent)
        if message is not None and str(message).strip():
            payload["history"].append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": agent,
                    "message": message,
                }
            )
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["version"] = payload.get("version", 0) + 1
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        """Persist *payload* and maintain LRU order + size cap."""
        task_id = payload["task_id"]
        self._memory[task_id] = payload
        if hasattr(self._memory, "move_to_end"):
            self._memory.move_to_end(task_id)
            while len(self._memory) > self._max_size:
                oldest_key, _ = next(iter(self._memory.items()))
                del self._memory[oldest_key]
                logger.debug(
                    "InMemoryTaskStore evicted oldest entry (task_id=%s)", oldest_key
                )

    # ------------------------------------------------------------------
    # Public interface (matches legacy TaskStore API)
    # ------------------------------------------------------------------

    def create_task(self, prompt: str) -> dict[str, Any]:
        """Create a new task record and return it."""
        task_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "task_id": task_id,
            "task": prompt,
            "status": TASK_STATUS_IN_PROGRESS,
            "agents": [],
            "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "version": 0,
        }
        self._save(payload)
        return payload

    def get_task(self, task_id: Any) -> dict[str, Any]:
        """Return the task record for *task_id*.

        Accepts ``str`` or any object whose ``__str__`` returns the id
        (e.g. ``TaskId`` value objects).

        Raises:
            KeyError: if no task with the given id exists.
        """
        task_id = str(task_id)
        if task_id not in self._memory:
            raise KeyError(task_id)
        return self._memory[task_id]

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[str] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """Apply a partial update to an existing task and return the updated record.

        Raises:
            KeyError: if the task does not exist.
        """
        task_id = str(task_id)
        payload = self.get_task(task_id)
        payload = self._apply_update(payload, status=status, agent=agent, message=message)
        self._save(payload)
        return payload

    def delete_task(self, task_id: Any) -> None:
        """Remove a task from the store.

        No-op if the task does not exist.
        """
        task_id = str(task_id)
        self._memory.pop(task_id, None)
