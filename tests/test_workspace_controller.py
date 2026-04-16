"""Integration tests for ``backend.UI.REST.controllers.workspace``.

Exercises the GET / PATCH ``/v1/tasks/{task_id}/workspace-file`` endpoints
that back the inline-edit-at-human-gate UX (future-plan.md §0 / H-11).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
from backend.UI.REST.controllers.workspace import router as workspace_router


class _FakeTaskStore(TaskStorePort):
    def __init__(self, tasks: dict[str, dict[str, Any]]) -> None:
        self._tasks = {key: {"task_id": key, **value} for key, value in tasks.items()}

    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None:
        self._tasks[task_id.value] = {"task_id": task_id.value, **dict(initial_data)}

    def get_task(self, task_id: Any) -> dict[str, Any]:
        key = task_id.value if hasattr(task_id, "value") else str(task_id)
        if key not in self._tasks:
            raise KeyError(key)
        return {**self._tasks[key]}

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        key = task_id.value if hasattr(task_id, "value") else str(task_id)
        self._tasks.setdefault(key, {"task_id": key})


def _app_with_task(workspace_root: Path | None = None) -> tuple[FastAPI, TestClient]:
    """Build a minimal app with one known task whose workspace_root is set."""
    app = FastAPI()
    app.include_router(workspace_router)
    app.state.task_store = _FakeTaskStore({"t-1": {"status": "awaiting_human"}})
    client = TestClient(app)
    return app, client


def _seed_pipeline_json(
    monkeypatch: pytest.MonkeyPatch,
    artifacts_root: Path,
    task_id: str,
    workspace_root: Path,
    diff: dict[str, Any] | None = None,
) -> None:
    """Point ARTIFACTS_ROOT at a tmp dir + write a pipeline.json for the task."""
    from backend.UI.REST import task_instance

    monkeypatch.setattr(task_instance, "ARTIFACTS_ROOT", artifacts_root)
    task_dir = artifacts_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"workspace_root": str(workspace_root)}
    if diff is not None:
        payload["dev_workspace_diff"] = diff
    (task_dir / "pipeline.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def writeable_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _seed_pipeline_json(monkeypatch, artifacts, "t-1", workspace)
    return workspace


# ---------------------------------------------------------------------------
# GET workspace-diff
# ---------------------------------------------------------------------------


def test_get_workspace_diff_returns_stored_payload(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _seed_pipeline_json(
        monkeypatch,
        artifacts,
        "t-1",
        tmp_path / "ws",
        diff={
            "diff_text": "diff --git a/foo b/foo\n",
            "files_changed": ["foo"],
            "stats": {"added": 1, "removed": 0, "files": 1},
            "source": "git",
        },
    )
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/t-1/workspace-diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["files_changed"] == ["foo"]
    assert data["source"] == "git"


def test_get_workspace_diff_returns_empty_when_no_pipeline_json(tmp_path, monkeypatch):
    from backend.UI.REST import task_instance

    monkeypatch.setattr(task_instance, "ARTIFACTS_ROOT", tmp_path / "artifacts-empty")
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/t-1/workspace-diff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["files_changed"] == []
    assert data["stats"]["files"] == 0


def test_get_workspace_diff_404_on_unknown_task(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _seed_pipeline_json(monkeypatch, artifacts, "t-1", tmp_path / "ws")
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/missing/workspace-diff")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET workspace-file
# ---------------------------------------------------------------------------


def test_get_workspace_file_returns_content(writeable_workspace):
    target = writeable_workspace / "src" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hi')\n", encoding="utf-8")
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/t-1/workspace-file", params={"path": "src/foo.py"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "src/foo.py"
    assert body["content"] == "print('hi')\n"


def test_get_workspace_file_404_on_missing_file(writeable_workspace):
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/t-1/workspace-file", params={"path": "missing.py"})
    assert resp.status_code == 404


def test_get_workspace_file_400_on_path_traversal(writeable_workspace):
    _, client = _app_with_task()
    resp = client.get(
        "/v1/tasks/t-1/workspace-file",
        params={"path": "../../etc/passwd"},
    )
    assert resp.status_code == 400


def test_get_workspace_file_400_when_no_workspace_root(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "t-1").mkdir()
    (artifacts / "t-1" / "pipeline.json").write_text("{}", encoding="utf-8")
    from backend.UI.REST import task_instance
    monkeypatch.setattr(task_instance, "ARTIFACTS_ROOT", artifacts)
    _, client = _app_with_task()
    resp = client.get("/v1/tasks/t-1/workspace-file", params={"path": "x"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# PATCH workspace-file
# ---------------------------------------------------------------------------


def test_patch_writes_file_when_env_enabled(writeable_workspace, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "new_file.txt", "content": "hello world\n"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["path"] == "new_file.txt"
    assert (writeable_workspace / "new_file.txt").read_text() == "hello world\n"


def test_patch_creates_parent_directories(writeable_workspace, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "deep/nested/dir/file.txt", "content": "abc"},
    )
    assert resp.status_code == 200
    assert (writeable_workspace / "deep" / "nested" / "dir" / "file.txt").read_text() == "abc"


def test_patch_403_when_env_disabled(writeable_workspace, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "0")
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "should_not_appear.txt", "content": "boom"},
    )
    assert resp.status_code == 403
    assert not (writeable_workspace / "should_not_appear.txt").exists()


def test_patch_403_when_env_unset(writeable_workspace, monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_WORKSPACE_WRITE", raising=False)
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "x.txt", "content": "y"},
    )
    assert resp.status_code == 403


def test_patch_400_on_path_traversal(writeable_workspace, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "../../etc/passwd", "content": "pwned"},
    )
    assert resp.status_code == 400


def test_patch_404_on_unknown_task(writeable_workspace, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/missing/workspace-file",
        json={"path": "x.txt", "content": "y"},
    )
    assert resp.status_code == 404


def test_patch_400_when_no_workspace_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ALLOW_WORKSPACE_WRITE", "1")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "t-1").mkdir()
    (artifacts / "t-1" / "pipeline.json").write_text("{}", encoding="utf-8")
    from backend.UI.REST import task_instance
    monkeypatch.setattr(task_instance, "ARTIFACTS_ROOT", artifacts)
    _, client = _app_with_task()
    resp = client.patch(
        "/v1/tasks/t-1/workspace-file",
        json={"path": "x.txt", "content": "y"},
    )
    assert resp.status_code == 400
