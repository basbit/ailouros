from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.tasks.domain.task_entity import TaskEvent


@dataclass
class AppendTaskEventCommand:
    task_id: TaskId
    agent: str
    message: str
    event_type: str
    new_status: Optional[TaskStatus] = None


class AppendTaskEventUseCase:
    def __init__(self, task_store: TaskStorePort) -> None:
        self._store = task_store

    def execute(self, cmd: AppendTaskEventCommand) -> TaskEvent:
        event = TaskEvent.now(cmd.agent, cmd.message, cmd.event_type)
        self._store.update_task(
            cmd.task_id,
            status=cmd.new_status,
            message=cmd.message,
            agent=cmd.agent,
        )
        return event
