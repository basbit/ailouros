"""CreateTaskUseCase — create a new task aggregate in the task store.

Rules (INV-7): application layer — no fastapi/redis/httpx/openai/anthropic/langgraph.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.tasks.domain.task_entity import Task, TaskEvent


@dataclass
class CreateTaskCommand:
    """Input for CreateTaskUseCase."""

    task_id: TaskId
    prompt: str
    initial_agents: list[str]


class CreateTaskUseCase:
    """Create and persist a new Task aggregate.

    Creates an initial status_change event and stores the task via
    TaskStorePort.
    """

    def __init__(self, task_store: TaskStorePort) -> None:
        self._store = task_store

    def execute(self, cmd: CreateTaskCommand) -> Task:
        """Create a task and return the Task aggregate.

        Args:
            cmd: CreateTaskCommand with task_id, prompt, and initial agents.

        Returns:
            The created Task aggregate (already persisted via task_store).
        """
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
