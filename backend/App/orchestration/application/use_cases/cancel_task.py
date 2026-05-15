
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort

logger = logging.getLogger(__name__)


@dataclass
class CancelTaskCommand:
    task_id: TaskId


@dataclass
class CancelTaskResult:
    task_id: TaskId
    status: TaskStatus
    was_active: bool
    message: str = ""


class CancelTaskUseCase:

    def __init__(
        self,
        task_store: TaskStorePort,
        cancel_event_fn: Any,
    ) -> None:
        self._task_store = task_store
        self._cancel_event_fn = cancel_event_fn

    def execute(self, command: CancelTaskCommand) -> CancelTaskResult:
        tid = command.task_id
        logger.info("CancelTaskUseCase.execute: task_id=%s", tid)

        try:
            task = self._task_store.get_task(tid)
        except KeyError:
            return CancelTaskResult(
                task_id=tid,
                status=TaskStatus.FAILED,
                was_active=False,
                message=f"Task {tid} not found",
            )

        current_status = task.get("status", "")
        was_active = current_status == TaskStatus.IN_PROGRESS.value

        self._cancel_event_fn(tid.value)

        self._task_store.update_task(
            tid,
            status=TaskStatus.CANCELLED,
            agent="orchestrator",
            message="Cancelled by user request",
        )

        logger.info(
            "task_cancelled: task_id=%s was_active=%s previous_status=%s",
            tid,
            was_active,
            current_status,
        )
        return CancelTaskResult(
            task_id=tid,
            status=TaskStatus.CANCELLED,
            was_active=was_active,
        )
