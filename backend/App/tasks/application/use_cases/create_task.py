from __future__ import annotations

from dataclasses import dataclass

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.tasks.domain.task_entity import Task, TaskEvent


@dataclass
class CreateTaskCommand:
    task_id: TaskId
    prompt: str
    initial_agents: list[str]


class CreateTaskUseCase:
    def __init__(self, task_store: TaskStorePort) -> None:
        self._store = task_store

    def execute(self, cmd: CreateTaskCommand) -> Task:
        task = Task(
            task_id=cmd.task_id.value,
            prompt=cmd.prompt,
            status=TaskStatus.IN_PROGRESS,
            agents=list(cmd.initial_agents),
        )
        task.append_event(
            TaskEvent.now("system", "task created", "status_change")
        )
        self._store.create_task(cmd.task_id, task.to_dict())
        return task
