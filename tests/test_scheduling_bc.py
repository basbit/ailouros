"""Tests for Scheduling BC (H-5): FireScheduleJobUseCase."""

from __future__ import annotations

from typing import Any, Optional

from backend.App.scheduling.domain.ports import ScheduleStorePort
from backend.App.scheduling.application.use_cases.fire_schedule_job import (
    FireScheduleJobCommand,
    FireScheduleJobUseCase,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeScheduleStore(ScheduleStorePort):
    """In-memory schedule store for testing."""

    def __init__(self, jobs: Optional[dict[str, dict[str, Any]]] = None) -> None:
        self._jobs: dict[str, dict[str, Any]] = dict(jobs or {})
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def get_job(self, schedule_id: str) -> Optional[dict[str, Any]]:
        return dict(self._jobs[schedule_id]) if schedule_id in self._jobs else None

    def update_job(self, schedule_id: str, **kwargs: Any) -> None:
        if schedule_id in self._jobs:
            self._jobs[schedule_id].update(kwargs)
        self.updates.append((schedule_id, kwargs))

    def list_jobs(self) -> list[dict[str, Any]]:
        return list(self._jobs.values())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFireScheduleJobUseCase:
    def test_fire_schedule_job_success(self) -> None:
        store = FakeScheduleStore({
            "sched-1": {"prompt": "hello", "enabled": True},
        })
        task_ids: list[str] = []

        def runner(prompt: str, job: dict) -> str:
            task_ids.append("task-99")
            return "task-99"

        uc = FireScheduleJobUseCase(schedule_store=store, pipeline_runner_fn=runner)
        result = uc.execute(FireScheduleJobCommand(schedule_id="sched-1"))

        assert result.status == "fired"
        assert result.task_id == "task-99"
        assert result.error is None
        assert task_ids == ["task-99"]
        # last_run should have been updated
        assert "last_run" in store.updates[-1][1]

    def test_fire_schedule_job_not_found(self) -> None:
        store = FakeScheduleStore({})
        uc = FireScheduleJobUseCase(
            schedule_store=store,
            pipeline_runner_fn=lambda p, j: "unused",
        )
        result = uc.execute(FireScheduleJobCommand(schedule_id="missing"))

        assert result.status == "skipped"
        assert result.error == "schedule not found"
        assert result.task_id is None

    def test_fire_schedule_job_disabled(self) -> None:
        store = FakeScheduleStore({
            "sched-2": {"prompt": "hello", "enabled": False},
        })
        uc = FireScheduleJobUseCase(
            schedule_store=store,
            pipeline_runner_fn=lambda p, j: "unused",
        )
        result = uc.execute(FireScheduleJobCommand(schedule_id="sched-2"))

        assert result.status == "skipped"
        assert result.error == "schedule disabled"

    def test_fire_schedule_job_runner_failure(self) -> None:
        store = FakeScheduleStore({
            "sched-3": {"prompt": "fail me", "enabled": True},
        })

        def failing_runner(prompt: str, job: dict) -> str:
            raise RuntimeError("LLM unreachable")

        uc = FireScheduleJobUseCase(
            schedule_store=store,
            pipeline_runner_fn=failing_runner,
        )
        result = uc.execute(FireScheduleJobCommand(schedule_id="sched-3"))

        assert result.status == "failed"
        assert "LLM unreachable" in (result.error or "")

    def test_override_prompt_is_passed_to_runner(self) -> None:
        store = FakeScheduleStore({
            "sched-4": {"prompt": "original", "enabled": True},
        })
        received: list[str] = []

        def runner(prompt: str, job: dict) -> str:
            received.append(prompt)
            return "task-42"

        uc = FireScheduleJobUseCase(
            schedule_store=store,
            pipeline_runner_fn=runner,
        )
        uc.execute(FireScheduleJobCommand(
            schedule_id="sched-4",
            override_prompt="override prompt",
        ))

        assert received == ["override prompt"]

    def test_schedule_store_port_is_abstract(self) -> None:
        """ScheduleStorePort cannot be instantiated directly."""
        import pytest
        with pytest.raises(TypeError):
            ScheduleStorePort()  # type: ignore[abstract]
