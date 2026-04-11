"""Tests for H-4 use-cases Wave B (resume_after_human, retry_pipeline, build_workspace_context)."""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import MagicMock

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.App.workspace.domain.ports import (
    FileEntry,
    ReadResult,
    WorkspaceContextMode,
    WorkspaceIOPort,
)
from backend.App.orchestration.application.use_cases.resume_after_human import (
    ResumeAfterHumanApprovalUseCase,
    ResumeAfterHumanCommand,
)
from backend.App.orchestration.application.use_cases.retry_pipeline import (
    RetryPipelineCommand,
    RetryPipelineFromFailedStepUseCase,
    _apply_retry_with,
)
from backend.App.workspace.application.use_cases.build_workspace_context import (
    BuildWorkspaceContextConfig,
    BuildWorkspaceContextUseCase,
)


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

    def update_task(self, task_id: TaskId, *, status=None, agent=None, message=None) -> None:
        if task_id.value not in self._tasks:
            self._tasks[task_id.value] = {}
        if status is not None:
            self._tasks[task_id.value]["status"] = status.value if hasattr(status, "value") else status
        if agent is not None:
            self._tasks[task_id.value]["agent"] = agent
        if message is not None:
            self._tasks[task_id.value]["message"] = message
        self.updates.append((task_id, status, agent, message))


class FakeWorkspaceIO(WorkspaceIOPort):
    """In-memory workspace IO for testing."""

    def __init__(self, files: Optional[dict[str, str]] = None) -> None:
        self._files = files or {}

    def list(self, path: str = "", *, max_depth: int = 3, max_files: int = 500) -> list[FileEntry]:
        return [
            FileEntry(path=p, size_bytes=len(c.encode()))
            for p, c in list(self._files.items())[:max_files]
        ]

    def read(self, path: str, *, max_chars: int = 50_000) -> ReadResult:
        if path not in self._files:
            raise FileNotFoundError(path)
        content = self._files[path][:max_chars]
        return ReadResult(
            content=content,
            truncated=len(self._files[path]) > max_chars,
            original_bytes=len(self._files[path].encode()),
        )

    def diff(self, path: str, from_ref: str, to_ref: str, *, max_chars: int = 20_000) -> str:
        return f"diff {path} {from_ref}..{to_ref}"

    def write(self, path: str, content: str) -> None:
        self._files[path] = content


# ---------------------------------------------------------------------------
# ResumeAfterHumanApprovalUseCase
# ---------------------------------------------------------------------------

class TestResumeAfterHumanApprovalUseCase:
    def _make_task_id(self) -> TaskId:
        return TaskId("test-task-1")

    def _make_store(self) -> FakeTaskStore:
        store = FakeTaskStore()
        store.create_task(TaskId("test-task-1"), {"status": "awaiting_human"})
        return store

    def test_success_returns_completed_status(self) -> None:
        store = self._make_store()
        runner = MagicMock(return_value={"qa_output": "All tests pass"})
        uc = ResumeAfterHumanApprovalUseCase(task_store=store, pipeline_runner=runner)

        cmd = ResumeAfterHumanCommand(
            task_id=self._make_task_id(),
            feedback="approved",
            partial_state={"input": "do something"},
            resume_from_step="qa",
        )
        result = uc.execute(cmd)

        assert result.status == TaskStatus.COMPLETED
        assert result.final_text == "All tests pass"
        assert result.last_agent == "qa"

    def test_success_updates_task_store_to_completed(self) -> None:
        store = self._make_store()
        runner = MagicMock(return_value={"qa_output": "done"})
        uc = ResumeAfterHumanApprovalUseCase(task_store=store, pipeline_runner=runner)

        cmd = ResumeAfterHumanCommand(
            task_id=self._make_task_id(),
            feedback="yes",
            partial_state={},
        )
        uc.execute(cmd)

        task = store._tasks["test-task-1"]
        assert task["status"] == TaskStatus.COMPLETED.value

    def test_failure_returns_failed_status(self) -> None:
        store = self._make_store()
        runner = MagicMock(side_effect=RuntimeError("LLM timeout"))
        uc = ResumeAfterHumanApprovalUseCase(task_store=store, pipeline_runner=runner)

        cmd = ResumeAfterHumanCommand(
            task_id=self._make_task_id(),
            feedback="approved",
            partial_state={},
        )
        result = uc.execute(cmd)

        assert result.status == TaskStatus.FAILED
        assert "LLM timeout" in result.error
        assert result.exc_type == "RuntimeError"

    def test_human_approval_required_returns_awaiting_status(self) -> None:
        from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

        store = self._make_store()
        exc = HumanApprovalRequired(step="qa", detail="needs approval", partial_state={"x": 1})
        runner = MagicMock(side_effect=exc)
        uc = ResumeAfterHumanApprovalUseCase(task_store=store, pipeline_runner=runner)

        cmd = ResumeAfterHumanCommand(
            task_id=self._make_task_id(),
            feedback="approved",
            partial_state={},
        )
        result = uc.execute(cmd)

        assert result.status == TaskStatus.AWAITING_HUMAN
        assert result.human_approval_step == "qa"

    def test_runner_receives_feedback_and_partial_state(self) -> None:
        store = self._make_store()
        runner = MagicMock(return_value={})
        uc = ResumeAfterHumanApprovalUseCase(task_store=store, pipeline_runner=runner)

        state = {"step": "dev", "output": "partial"}
        cmd = ResumeAfterHumanCommand(
            task_id=self._make_task_id(),
            feedback="looks good",
            partial_state=state,
            resume_from_step="qa",
        )
        uc.execute(cmd)

        runner.assert_called_once()
        call_args = runner.call_args
        assert call_args[0][0] == state  # partial_state
        assert call_args[0][1] == "qa"   # resume_from_step
        assert call_args[0][2] == "looks good"  # feedback


# ---------------------------------------------------------------------------
# RetryPipelineFromFailedStepUseCase
# ---------------------------------------------------------------------------

class TestRetryPipelineFromFailedStepUseCase:
    def _make_task_id(self) -> TaskId:
        return TaskId("retry-task-1")

    def _make_store(self) -> FakeTaskStore:
        store = FakeTaskStore()
        store.create_task(TaskId("retry-task-1"), {"status": "failed"})
        return store

    def test_success_returns_completed(self) -> None:
        store = self._make_store()
        runner = MagicMock(return_value={"dev_output": "fixed code"})
        uc = RetryPipelineFromFailedStepUseCase(task_store=store, pipeline_runner=runner)

        cmd = RetryPipelineCommand(
            task_id=self._make_task_id(),
            failed_step="dev",
            partial_state={"input": "write tests"},
        )
        result = uc.execute(cmd)

        assert result.status == TaskStatus.COMPLETED
        assert result.final_text == "fixed code"
        assert result.last_agent == "dev"

    def test_failure_returns_failed_status(self) -> None:
        store = self._make_store()
        runner = MagicMock(side_effect=ValueError("invalid state"))
        uc = RetryPipelineFromFailedStepUseCase(task_store=store, pipeline_runner=runner)

        cmd = RetryPipelineCommand(
            task_id=self._make_task_id(),
            failed_step="qa",
            partial_state={},
        )
        result = uc.execute(cmd)

        assert result.status == TaskStatus.FAILED
        assert "invalid state" in result.error

    def test_retry_with_different_model(self) -> None:
        store = self._make_store()
        captured_ac: dict = {}

        def runner(state, step, *, agent_config=None, **kw):
            captured_ac.update(agent_config or {})
            return {"pm_output": "done"}

        uc = RetryPipelineFromFailedStepUseCase(task_store=store, pipeline_runner=runner)
        cmd = RetryPipelineCommand(
            task_id=self._make_task_id(),
            failed_step="pm",
            partial_state={},
            retry_with={"different_model": "mistral:7b"},
            agent_config={"pm": {"model": "llama"}},
        )
        result = uc.execute(cmd)
        assert result.status == TaskStatus.COMPLETED
        # Model should be overridden
        assert captured_ac.get("pm", {}).get("model") == "mistral:7b"

    def test_retry_with_tools_off(self) -> None:
        state, ac = _apply_retry_with(
            {"x": 1}, {"tools_off": True}, {"swarm": {"mcp_auto": True}}
        )
        assert ac["swarm"]["mcp_auto"] is False

    def test_retry_with_reduced_context(self) -> None:
        state, ac = _apply_retry_with(
            {}, {"reduced_context": True}, {}
        )
        assert ac["swarm"]["workspace_context_mode"] == "index_only"

    def test_retry_with_empty_dict_is_noop(self) -> None:
        original_state = {"k": "v"}
        state, ac = _apply_retry_with(original_state, {}, {"pm": {"model": "x"}})
        assert state == original_state
        assert ac == {"pm": {"model": "x"}}

    def test_task_store_updated_to_in_progress_then_completed(self) -> None:
        store = self._make_store()
        runner = MagicMock(return_value={})
        uc = RetryPipelineFromFailedStepUseCase(task_store=store, pipeline_runner=runner)

        cmd = RetryPipelineCommand(
            task_id=self._make_task_id(),
            failed_step="dev",
            partial_state={},
        )
        uc.execute(cmd)

        statuses = [u[1] for u in store.updates if u[1] is not None]
        assert TaskStatus.IN_PROGRESS in statuses
        assert TaskStatus.COMPLETED in statuses


# ---------------------------------------------------------------------------
# BuildWorkspaceContextUseCase
# ---------------------------------------------------------------------------

class TestBuildWorkspaceContextUseCase:
    def _make_io(self) -> FakeWorkspaceIO:
        return FakeWorkspaceIO({
            "src/main.py": "def main(): pass",
            "tests/test_main.py": "def test_main(): pass",
            "README.md": "# Project",
        })

    def test_retrieve_mcp_returns_stub_no_file_bodies(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.RETRIEVE_MCP, mcp_available=True)

        assert result.context_mode == WorkspaceContextMode.RETRIEVE_MCP
        assert "MCP" in result.snapshot or "mcp" in result.snapshot.lower()
        assert result.stats["files_included"] == 0
        assert not result.fallback_applied

    def test_retrieve_mcp_falls_back_to_retrieve_fs_when_unavailable(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.RETRIEVE_MCP, mcp_available=False)

        assert result.context_mode == WorkspaceContextMode.RETRIEVE_FS
        assert result.fallback_applied is True
        assert "RETRIEVE_MCP" in result.fallback_reason or "retrieve_mcp" in result.fallback_reason.lower()

    def test_index_only_lists_files_no_bodies(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.INDEX_ONLY)

        assert result.context_mode == WorkspaceContextMode.INDEX_ONLY
        assert "src/main.py" in result.snapshot
        assert "def main()" not in result.snapshot
        assert result.stats["files_included"] == 0

    def test_full_mode_includes_file_bodies(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.FULL)

        assert result.context_mode == WorkspaceContextMode.FULL
        assert "def main()" in result.snapshot
        assert result.stats["files_included"] > 0

    def test_priority_paths_mode_filters_by_prefix(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute(
            "/proj",
            WorkspaceContextMode.PRIORITY_PATHS,
            priority_paths=["src/"],
        )

        assert result.context_mode == WorkspaceContextMode.PRIORITY_PATHS
        assert "def main()" in result.snapshot
        # tests/ should not be included when priority_paths=["src/"]
        assert "def test_main()" not in result.snapshot

    def test_retrieve_fs_returns_index_no_bodies(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.RETRIEVE_FS)

        assert result.context_mode == WorkspaceContextMode.RETRIEVE_FS
        assert "src/main.py" in result.snapshot
        assert "def main()" not in result.snapshot

    def test_snapshot_char_limit_respected(self) -> None:
        large_content = "x" * 1000
        io = FakeWorkspaceIO({f"file{i}.py": large_content for i in range(100)})
        cfg = BuildWorkspaceContextConfig(max_snapshot_chars=5000)
        uc = BuildWorkspaceContextUseCase(workspace_io=io, config=cfg)

        result = uc.execute("/proj", WorkspaceContextMode.FULL)

        # Should not exceed limit by much (one file may push slightly over)
        assert result.stats.get("files_included", 0) < 100

    def test_fallback_not_applied_when_mcp_available(self) -> None:
        io = self._make_io()
        uc = BuildWorkspaceContextUseCase(workspace_io=io)

        result = uc.execute("/proj", WorkspaceContextMode.RETRIEVE_MCP, mcp_available=True)

        assert result.fallback_applied is False
        assert result.fallback_reason == ""
