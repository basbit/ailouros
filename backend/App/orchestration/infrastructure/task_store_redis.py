
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort

logger = logging.getLogger(__name__)


class TaskStoreAdapter(TaskStorePort):

    def __init__(self, store: Any) -> None:
        self._store = store

    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None:
        prompt = initial_data.get("prompt", "")
        task = self._store.create_task(prompt)
        if task.get("task_id") != task_id.value:
            logger.warning(
                "TaskStoreAdapter: requested task_id=%r but store allocated task_id=%r",
                task_id.value,
                task.get("task_id"),
            )

    def get_task(self, task_id: TaskId) -> dict[str, Any]:
        return self._store.get_task(task_id.value)

    def update_task(
        self,
        task_id: TaskId,
        *,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status.value
        if agent is not None:
            kwargs["agent"] = agent
        if message is not None:
            kwargs["message"] = message
        if kwargs:
            self._store.update_task(task_id.value, **kwargs)
