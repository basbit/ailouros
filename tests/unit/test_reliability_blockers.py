"""Тесты блокеров zero-writes и failed trusted gates в финализации."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


class _StubTaskStore:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.task_id = "stub-task"

    def update_task(self, task_id: str, **kwargs: Any) -> None:
        self.updates.append({"task_id": task_id, **kwargs})


def _zero_write_snapshot() -> dict[str, Any]:
    return {
        "user_prompt": "do thing",
        "input": "do thing",
        "agent_config": {},
        "pipeline_steps": ["dev"],
        "workspace": {},
        "workspace_writes": {"written": [], "patched": [], "udiff_applied": [], "parsed": 0},
        "dev_mcp_write_count": 0,
        "_ec1_zero_writes": True,
        "_ec1_error": "Dev step produced 0 workspace writes with apply_writes=True.",
    }


def _failed_gates_snapshot() -> dict[str, Any]:
    return {
        "user_prompt": "x",
        "input": "x",
        "agent_config": {},
        "pipeline_steps": ["dev"],
        "workspace": {},
        "workspace_writes": {"written": ["a.txt"], "patched": [], "udiff_applied": [], "parsed": 1},
        "dev_mcp_write_count": 0,
        "_failed_trusted_gates": ["build_gate"],
        "_failed_trusted_gates_summary": "build_gate: BUILD_FAILED",
    }


@pytest.fixture
def stub_store() -> _StubTaskStore:
    return _StubTaskStore()


def _run_start_pipeline(snapshot: dict[str, Any], task_store: _StubTaskStore, tmp_path: Path):
    from backend.App.orchestration.application.use_cases import tasks as tasks_module

    artifacts_root = tmp_path
    task_dir = artifacts_root / "stub-task"
    task_dir.mkdir(parents=True, exist_ok=True)

    def _fake_run_pipeline(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return snapshot

    def _fake_final_text(*_args: Any, **_kwargs: Any) -> str:
        return ""

    def _fake_agent_label(*_args: Any, **_kwargs: Any) -> str:
        return "dev"

    def _fake_get_setting_bool(key: str, **_kwargs: Any) -> bool:
        return True

    def _fake_workspace_write_allowed() -> bool:
        return True

    def _fake_apply_writes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return snapshot.get("workspace_writes") or {}

    def _fake_run_shell(*_args: Any, **_kwargs: Any):
        return None

    def _fake_followup(*_args: Any, **_kwargs: Any) -> list[str]:
        return []

    def _identity(value: Any) -> Any:
        return value

    with patch.object(tasks_module, "run_pipeline", _fake_run_pipeline), \
         patch.object(tasks_module, "final_pipeline_user_message", _fake_final_text), \
         patch.object(tasks_module, "task_store_agent_label", _fake_agent_label), \
         patch.object(tasks_module, "get_setting_bool", _fake_get_setting_bool), \
         patch.object(tasks_module, "workspace_write_allowed", _fake_workspace_write_allowed), \
         patch.object(tasks_module, "apply_from_devops_and_dev_outputs", _fake_apply_writes), \
         patch.object(tasks_module, "run_shell_after_user_approval", _fake_run_shell):
        with pytest.warns(DeprecationWarning):
            return tasks_module.start_pipeline_run(
                user_prompt="do thing",
                effective_prompt="do thing",
                agent_config={},
                steps=["dev"],
                workspace_root_str=str(tmp_path / "workspace"),
                workspace_apply_writes=True,
                workspace_path=tmp_path / "workspace",
                workspace_meta={},
                task_id="stub-task",
                task_store=task_store,
                artifacts_root=artifacts_root,
                pipeline_snapshot_for_disk=_identity,
                workspace_followup_lines=_fake_followup,
                resolved_scenario=None,
            )


def test_zero_writes_marks_run_failed_by_default(tmp_path, stub_store):
    (tmp_path / "workspace").mkdir()
    result = _run_start_pipeline(_zero_write_snapshot(), stub_store, tmp_path)
    assert result["status"] == "failed"
    assert "0 workspace writes" in result.get("error", "")
    failed_updates = [u for u in stub_store.updates if u.get("status") == "failed"]
    assert failed_updates, f"expected a failed update, got {stub_store.updates}"


def test_zero_writes_marks_completed_no_writes_when_setting_disabled(tmp_path, stub_store):
    (tmp_path / "workspace").mkdir()
    from backend.App.orchestration.application.use_cases import tasks as tasks_module

    def _fake_setting(key: str, **_kwargs: Any) -> bool:
        if key == "swarm.require_dev_writes":
            return False
        return True

    snapshot = _zero_write_snapshot()
    artifacts_root = tmp_path
    (artifacts_root / "stub-task").mkdir(parents=True, exist_ok=True)

    def _fake_run_pipeline(*_a, **_k):
        return snapshot

    with patch.object(tasks_module, "run_pipeline", _fake_run_pipeline), \
         patch.object(tasks_module, "final_pipeline_user_message", lambda *_a, **_k: ""), \
         patch.object(tasks_module, "task_store_agent_label", lambda *_a, **_k: "dev"), \
         patch.object(tasks_module, "get_setting_bool", _fake_setting), \
         patch.object(tasks_module, "workspace_write_allowed", lambda: True), \
         patch.object(tasks_module, "apply_from_devops_and_dev_outputs", lambda *_a, **_k: snapshot.get("workspace_writes") or {}), \
         patch.object(tasks_module, "run_shell_after_user_approval", lambda *_a, **_k: None):
        with pytest.warns(DeprecationWarning):
            result = tasks_module.start_pipeline_run(
                user_prompt="x",
                effective_prompt="x",
                agent_config={},
                steps=["dev"],
                workspace_root_str=str(tmp_path / "workspace"),
                workspace_apply_writes=True,
                workspace_path=tmp_path / "workspace",
                workspace_meta={},
                task_id="stub-task",
                task_store=stub_store,
                artifacts_root=artifacts_root,
                pipeline_snapshot_for_disk=lambda v: v,
                workspace_followup_lines=lambda *_a, **_k: [],
                resolved_scenario=None,
            )
    assert result["status"] == "completed_no_writes"


def test_failed_trusted_gates_mark_run_failed_by_default(tmp_path, stub_store):
    (tmp_path / "workspace").mkdir()
    result = _run_start_pipeline(_failed_gates_snapshot(), stub_store, tmp_path)
    assert result["status"] == "failed"
    assert "build_gate" in result.get("error", "")
    failed_updates = [u for u in stub_store.updates if u.get("status") == "failed"]
    assert failed_updates


def test_clean_run_returns_completed(tmp_path, stub_store):
    (tmp_path / "workspace").mkdir()
    snapshot = {
        "user_prompt": "x",
        "input": "x",
        "agent_config": {},
        "pipeline_steps": ["dev"],
        "workspace": {},
        "workspace_writes": {
            "written": ["a.txt"], "patched": [], "udiff_applied": [], "parsed": 1,
        },
        "dev_mcp_write_count": 0,
    }
    result = _run_start_pipeline(snapshot, stub_store, tmp_path)
    assert result["status"] == "completed"
    assert "error" not in result
