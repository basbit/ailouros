"""Tests for E-3/E-4/E-5 use cases (StartPipelineRun, CancelTask, WorkspaceContextPolicy)."""

from __future__ import annotations

from typing import Any, Optional

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.workspace.domain.ports import (
    WorkspaceContextMode,
    WorkspaceContextPolicy,
    WorkspaceIOPort,
)
from backend.App.orchestration.application.use_cases.cancel_task import (
    CancelTaskCommand,
    CancelTaskUseCase,
)
from backend.App.orchestration.application.use_cases.start_pipeline_run import (
    StartPipelineRunCommand,
    StartPipelineRunUseCase,
)


# ---------------------------------------------------------------------------
# Fake ports for testing
# ---------------------------------------------------------------------------

class FakeTaskStore(TaskStorePort):
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}

    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None:
        self._tasks[task_id.value] = dict(initial_data)

    def get_task(self, task_id: TaskId) -> dict[str, Any]:
        try:
            return dict(self._tasks[task_id.value])
        except KeyError as exc:
            raise KeyError(task_id.value) from exc

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
            self._tasks[task_id.value]["status"] = status.value
        if agent is not None:
            self._tasks[task_id.value]["agent"] = agent
        if message is not None:
            self._tasks[task_id.value]["message"] = message


class FakeWorkspaceIO(WorkspaceIOPort):
    def list(self, path="", *, max_depth=3, max_files=500):
        return []

    def read(self, path, *, max_chars=50_000):
        from backend.App.workspace.domain.ports import ReadResult
        return ReadResult(content="", truncated=False, original_bytes=0)

    def diff(self, path, from_ref, to_ref, *, max_chars=20_000):
        return ""

    def write(self, path, content):
        raise PermissionError("write not allowed in tests")


# ---------------------------------------------------------------------------
# StartPipelineRunUseCase tests
# ---------------------------------------------------------------------------

def _make_command(task_id: str = "task-1") -> StartPipelineRunCommand:
    return StartPipelineRunCommand(
        task_id=TaskId(task_id),
        user_prompt="build X",
        effective_prompt="build X",
        agent_config={},
        steps=None,
        workspace_root_str="",
        workspace_apply_writes=False,
    )


def test_start_pipeline_run_success():
    store = FakeTaskStore()
    fake_result = {"qa_output": "all tests pass"}

    def fake_runner(*args, **kwargs):
        return fake_result

    uc = StartPipelineRunUseCase(store, FakeWorkspaceIO(), fake_runner)
    result = uc.execute(_make_command())

    assert result.status == TaskStatus.COMPLETED
    assert result.final_text == "all tests pass"
    assert result.last_agent == "qa"
    assert store._tasks["task-1"]["status"] == TaskStatus.COMPLETED.value


def test_start_pipeline_run_failure():
    store = FakeTaskStore()

    def failing_runner(*args, **kwargs):
        raise RuntimeError("GPU out of memory")

    uc = StartPipelineRunUseCase(store, FakeWorkspaceIO(), failing_runner)
    result = uc.execute(_make_command())

    assert result.status == TaskStatus.FAILED
    assert "GPU out of memory" in result.error
    assert result.exc_type == "RuntimeError"
    assert store._tasks["task-1"]["status"] == TaskStatus.FAILED.value


def test_start_pipeline_run_human_approval():
    from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

    store = FakeTaskStore()

    def approval_runner(*args, **kwargs):
        raise HumanApprovalRequired(
            "human_pm",
            "needs human review",
            partial_state={"pm_output": "draft"},
            resume_pipeline_step="pm",
        )

    uc = StartPipelineRunUseCase(store, FakeWorkspaceIO(), approval_runner)
    result = uc.execute(_make_command())

    assert result.status == TaskStatus.AWAITING_HUMAN
    assert result.human_approval_step == "human_pm"
    assert store._tasks["task-1"]["status"] == TaskStatus.AWAITING_HUMAN.value


# ---------------------------------------------------------------------------
# CancelTaskUseCase tests
# ---------------------------------------------------------------------------

def test_cancel_task_active():
    store = FakeTaskStore()
    store._tasks["task-2"] = {"status": "in_progress"}
    cancelled_ids: list[str] = []

    def fake_cancel(tid: str) -> bool:
        cancelled_ids.append(tid)
        return True

    uc = CancelTaskUseCase(store, fake_cancel)
    result = uc.execute(CancelTaskCommand(task_id=TaskId("task-2")))

    assert result.status == TaskStatus.CANCELLED
    assert result.was_active is True
    assert "task-2" in cancelled_ids
    assert store._tasks["task-2"]["status"] == TaskStatus.CANCELLED.value


def test_cancel_task_already_done():
    store = FakeTaskStore()
    store._tasks["task-3"] = {"status": "completed"}

    uc = CancelTaskUseCase(store, lambda _: False)
    result = uc.execute(CancelTaskCommand(task_id=TaskId("task-3")))

    assert result.status == TaskStatus.CANCELLED
    assert result.was_active is False


def test_cancel_task_not_found():
    store = FakeTaskStore()
    uc = CancelTaskUseCase(store, lambda _: False)
    result = uc.execute(CancelTaskCommand(task_id=TaskId("nonexistent")))

    assert result.status == TaskStatus.FAILED
    assert result.was_active is False


# ---------------------------------------------------------------------------
# WorkspaceContextPolicy tests (E-5)
# ---------------------------------------------------------------------------


def test_policy_upgrade_always_valid():
    # Moving from INDEX_ONLY to RETRIEVE_MCP is an upgrade → always valid
    assert WorkspaceContextPolicy.is_valid_transition(
        WorkspaceContextMode.INDEX_ONLY, WorkspaceContextMode.RETRIEVE_MCP
    )


def test_policy_same_mode_valid():
    assert WorkspaceContextPolicy.is_valid_transition(
        WorkspaceContextMode.PRIORITY_PATHS, WorkspaceContextMode.PRIORITY_PATHS
    )


def test_policy_one_step_downgrade_valid():
    # RETRIEVE_MCP → RETRIEVE_FS is one step down → valid
    assert WorkspaceContextPolicy.is_valid_transition(
        WorkspaceContextMode.RETRIEVE_MCP, WorkspaceContextMode.RETRIEVE_FS
    )


def test_policy_two_step_downgrade_invalid():
    # RETRIEVE_MCP → PRIORITY_PATHS skips RETRIEVE_FS → invalid
    assert not WorkspaceContextPolicy.is_valid_transition(
        WorkspaceContextMode.RETRIEVE_MCP, WorkspaceContextMode.PRIORITY_PATHS
    )


def test_policy_three_step_downgrade_invalid():
    assert not WorkspaceContextPolicy.is_valid_transition(
        WorkspaceContextMode.RETRIEVE_MCP, WorkspaceContextMode.INDEX_ONLY
    )
