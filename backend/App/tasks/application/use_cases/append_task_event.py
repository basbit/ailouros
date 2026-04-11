"""AppendTaskEventUseCase — append an event to a task's history.

Rules (INV-7): application layer — no fastapi/redis/httpx/openai/anthropic/langgraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.tasks.domain.task_entity import TaskEvent


@dataclass
class AppendTaskEventCommand:
    """Input for AppendTaskEventUseCase."""

    task_id: TaskId
    agent: str
    message: str
    event_type: str
    new_status: Optional[TaskStatus] = None


class AppendTaskEventUseCase:
    """Append a TaskEvent to a task and optionally update its status.

    Creates a timestamped TaskEvent and calls task_store.update_task
    with the new status (if provided).
    """

    def __init__(self, task_store: TaskStorePort) -> None:
        self._store = task_store

    def execute(self, cmd: AppendTaskEventCommand) -> TaskEvent:
        """Create and record a task event.

        Args:
            cmd: AppendTaskEventCommand with task_id, agent, message,
                 event_type, and optional new_status.

        Returns:
            The created TaskEvent.
        """
        event = TaskEvent.now(cmd.agent, cmd.message, cmd.event_type)
        self._store.update_task(
            cmd.task_id,
            status=cmd.new_status,
            message=cmd.message,
            agent=cmd.agent,
        )
        return event
