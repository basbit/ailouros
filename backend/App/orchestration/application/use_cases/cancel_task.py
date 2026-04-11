"""CancelTaskUseCase — application-layer task cancellation.

Strangler Fig: the existing ``POST /v1/tasks/{task_id}/cancel`` route in
``orchestrator/api/routes_tasks.py`` implements cancellation directly;
this use case provides a clean port-based interface for future migration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.App.orchestration.domain.ports import TaskId, TaskStatus, TaskStorePort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command / Result
# ---------------------------------------------------------------------------

@dataclass
class CancelTaskCommand:
    task_id: TaskId


@dataclass
class CancelTaskResult:
    task_id: TaskId
    status: TaskStatus
    was_active: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class CancelTaskUseCase:
    """Cancel an in-progress pipeline task.

    Delegates the actual cancel signal to ``cancel_event_fn`` (injected),
    which sets the per-task threading.Event so the pipeline loop stops.
    """

    def __init__(
        self,
        task_store: TaskStorePort,
        cancel_event_fn: Any,  # callable(task_id: str) -> bool
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

        # Signal the pipeline cancel event
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
