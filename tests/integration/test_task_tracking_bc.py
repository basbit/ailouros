"""Tests for Task Tracking BC (H-5): Task entity, CreateTaskUseCase, AppendTaskEventUseCase."""

from __future__ import annotations

from typing import Any, Optional

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.tasks.application.use_cases.append_task_event import (
    AppendTaskEventCommand,
    AppendTaskEventUseCase,
)
from backend.App.tasks.application.use_cases.create_task import (
    CreateTaskCommand,
    CreateTaskUseCase,
)
from backend.App.tasks.domain.task_entity import Task, TaskEvent


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeTaskStore(TaskStorePort):
    """In-memory task store for testing."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple] = []

    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None:
        self._tasks[task_id.value] = dict(initial_data)

    def get_task(self, task_id: TaskId) -> dict[str, Any]:
        if task_id.value not in self._tasks:
            raise KeyError(task_id.value)
        return dict(self._tasks[task_id.value])

    def update_task(
        self,
        task_id: TaskId,
        *,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        if task_id.value not in self._tasks:
            self._tasks[task_id.value] = {}
        if status is not None:
            self._tasks[task_id.value]["status"] = (
                status.value if hasattr(status, "value") else status
            )
        if agent is not None:
            self._tasks[task_id.value]["agent"] = agent
        if message is not None:
            self._tasks[task_id.value]["message"] = message
        self.updates.append((task_id, status, agent, message))


# ---------------------------------------------------------------------------
# TaskEvent tests
# ---------------------------------------------------------------------------

class TestTaskEvent:
    def test_task_event_now_has_timestamp(self) -> None:
        event = TaskEvent.now("system", "test message", "status_change")
        assert event.timestamp
        assert "T" in event.timestamp  # ISO 8601 format

    def test_task_event_now_fields(self) -> None:
        event = TaskEvent.now("pm", "starting pm step", "step_start")
        assert event.agent == "pm"
        assert event.message == "starting pm step"
        assert event.event_type == "step_start"

    def test_task_event_is_dataclass(self) -> None:
        event = TaskEvent(
            timestamp="2026-01-01T00:00:00+00:00",
            agent="dev",
            message="done",
            event_type="step_end",
        )
        assert event.agent == "dev"


# ---------------------------------------------------------------------------
# Task entity tests
# ---------------------------------------------------------------------------

class TestTaskEntity:
    def test_task_entity_to_dict(self) -> None:
        task = Task(
            task_id="t-1",
            prompt="do something",
            status=TaskStatus.IN_PROGRESS,
            agents=["pm", "dev"],
        )
        d = task.to_dict()
        assert d["task_id"] == "t-1"
        assert d["prompt"] == "do something"
        assert d["status"] == "in_progress"
        assert d["agents"] == ["pm", "dev"]
        assert isinstance(d["history"], list)
        assert isinstance(d["version"], int)

    def test_append_event_increments_version(self) -> None:
        task = Task(
            task_id="t-2",
            prompt="test",
            status=TaskStatus.IN_PROGRESS,
        )
        initial_version = task.version
        event = TaskEvent.now("system", "event", "status_change")
        task.append_event(event)
        assert task.version == initial_version + 1
        assert len(task.history) == 1

    def test_append_multiple_events(self) -> None:
        task = Task(
            task_id="t-3",
            prompt="test",
            status=TaskStatus.IN_PROGRESS,
        )
        for i in range(3):
            task.append_event(TaskEvent.now("agent", f"step {i}", "step_start"))
        assert len(task.history) == 3
        assert task.version == 4  # 1 initial + 3

    def test_to_dict_serializes_history(self) -> None:
        task = Task(
            task_id="t-4",
            prompt="test",
            status=TaskStatus.COMPLETED,
        )
        task.append_event(TaskEvent.now("qa", "tests passed", "step_end"))
        d = task.to_dict()
        assert len(d["history"]) == 1
        h = d["history"][0]
        assert h["agent"] == "qa"
        assert h["event_type"] == "step_end"


# ---------------------------------------------------------------------------
# CreateTaskUseCase tests
# ---------------------------------------------------------------------------

class TestCreateTaskUseCase:
    def test_create_task_stores_task(self) -> None:
        store = FakeTaskStore()
        uc = CreateTaskUseCase(task_store=store)

        cmd = CreateTaskCommand(
            task_id=TaskId("task-abc"),
            prompt="do something",
            initial_agents=["pm", "dev"],
        )
        task = uc.execute(cmd)

        assert task.task_id == "task-abc"
        assert task.status == TaskStatus.IN_PROGRESS
        assert "pm" in task.agents
        # Task should be in store
        stored = store._tasks["task-abc"]
        assert stored["prompt"] == "do something"

    def test_create_task_returns_task_aggregate(self) -> None:
        store = FakeTaskStore()
        uc = CreateTaskUseCase(task_store=store)

        cmd = CreateTaskCommand(
            task_id=TaskId("task-def"),
            prompt="build feature",
            initial_agents=["architect"],
        )
        task = uc.execute(cmd)

        assert isinstance(task, Task)
        assert len(task.history) == 1  # initial status_change event
        assert task.history[0].event_type == "status_change"

    def test_create_task_initial_event_is_system(self) -> None:
        store = FakeTaskStore()
        uc = CreateTaskUseCase(task_store=store)

        cmd = CreateTaskCommand(
            task_id=TaskId("task-ghi"),
            prompt="test",
            initial_agents=[],
        )
        task = uc.execute(cmd)

        event = task.history[0]
        assert event.agent == "system"
        assert event.message == "task created"


# ---------------------------------------------------------------------------
# AppendTaskEventUseCase tests
# ---------------------------------------------------------------------------

class TestAppendTaskEventUseCase:
    def test_append_task_event_updates_store(self) -> None:
        store = FakeTaskStore()
        store.create_task(TaskId("task-1"), {"status": "in_progress"})
        uc = AppendTaskEventUseCase(task_store=store)

        cmd = AppendTaskEventCommand(
            task_id=TaskId("task-1"),
            agent="dev",
            message="writing code",
            event_type="step_start",
        )
        event = uc.execute(cmd)

        assert isinstance(event, TaskEvent)
        assert event.agent == "dev"
        assert event.message == "writing code"

    def test_append_task_event_with_status_change(self) -> None:
        store = FakeTaskStore()
        store.create_task(TaskId("task-2"), {"status": "in_progress"})
        uc = AppendTaskEventUseCase(task_store=store)

        cmd = AppendTaskEventCommand(
            task_id=TaskId("task-2"),
            agent="qa",
            message="tests failed",
            event_type="error",
            new_status=TaskStatus.FAILED,
        )
        uc.execute(cmd)

        stored = store._tasks["task-2"]
        assert stored["status"] == "failed"

    def test_append_task_event_returns_event_with_timestamp(self) -> None:
        store = FakeTaskStore()
        store.create_task(TaskId("task-3"), {"status": "in_progress"})
        uc = AppendTaskEventUseCase(task_store=store)

        cmd = AppendTaskEventCommand(
            task_id=TaskId("task-3"),
            agent="pm",
            message="plan complete",
            event_type="step_end",
        )
        event = uc.execute(cmd)

        assert event.timestamp
        assert "T" in event.timestamp
