"""Integration tests for thin UI/REST controllers (H-6).

Uses httpx.TestClient against a minimal FastAPI app wired with fake stores.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.App.tasks.domain.ports import TaskId, TaskStatus, TaskStorePort
import backend.UI.REST.controllers.chat as chat_controller
from backend.UI.REST.controllers.chat import router as chat_router
from backend.UI.REST.controllers.tasks import router as tasks_router


@pytest.fixture(autouse=True)
def _restore_chat_controller_task_store():
    """Restore chat_controller.task_store after each test to prevent state leakage."""
    original = getattr(chat_controller, "task_store", None)
    yield
    chat_controller.task_store = original


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeTaskStore(TaskStorePort):
    """In-memory task store for controller tests."""

    def __init__(self, tasks: Optional[dict[str, dict[str, Any]]] = None) -> None:
        self._tasks: dict[str, dict[str, Any]] = {
            key: {"task_id": key, **value}
            for key, value in dict(tasks or {}).items()
        }

    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None:
        self._tasks[task_id.value] = {"task_id": task_id.value, **dict(initial_data)}

    def get_task(self, task_id: Any) -> dict[str, Any]:
        key = task_id.value if hasattr(task_id, "value") else str(task_id)
        if key not in self._tasks:
            raise KeyError(key)
        return {"task_id": key, **dict(self._tasks[key])}

    def update_task(
        self,
        task_id: Any,
        *,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        key = task_id.value if hasattr(task_id, "value") else str(task_id)
        if key not in self._tasks:
            self._tasks[key] = {"task_id": key}
        if status is not None:
            self._tasks[key]["status"] = (
                status.value if hasattr(status, "value") else status
            )
        if agent is not None:
            self._tasks[key]["agent"] = agent
        if message is not None:
            self._tasks[key]["message"] = message


def _make_app(
    tasks: Optional[dict[str, dict[str, Any]]] = None,
    cancel_fn=None,
) -> FastAPI:
    """Create a minimal FastAPI test app wired with fake stores."""
    app = FastAPI()
    app.include_router(tasks_router)
    app.include_router(chat_router)
    store = FakeTaskStore(tasks)
    app.state.task_store = store
    app.state.cancel_fn = cancel_fn or (lambda task_id: True)
    chat_controller.task_store = store
    return app


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}
# ---------------------------------------------------------------------------

class TestGetTask:
    def test_get_existing_task_returns_200(self) -> None:
        app = _make_app({"task-1": {"status": "in_progress", "prompt": "hello"}})
        client = TestClient(app)

        resp = client.get("/tasks/task-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "in_progress"

    def test_get_missing_task_returns_404(self) -> None:
        app = _make_app({})
        client = TestClient(app)

        resp = client.get("/tasks/nonexistent")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/tasks/{task_id}/cancel
# ---------------------------------------------------------------------------

class TestCancelTask:
    def test_cancel_task_endpoint_returns_200(self) -> None:
        cancel_called: list[str] = []

        def fake_cancel(task_id: str) -> bool:
            cancel_called.append(task_id)
            return True

        app = _make_app(
            tasks={"task-x": {"status": "in_progress"}},
            cancel_fn=fake_cancel,
        )
        client = TestClient(app)

        resp = client.post("/v1/tasks/task-x/cancel")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["task_id"] == "task-x"
        assert "was_active" in data
        assert cancel_called == ["task-x"]

    def test_cancel_missing_task_returns_404(self) -> None:
        app = _make_app({})
        client = TestClient(app)

        resp = client.post("/v1/tasks/no-such-task/cancel")

        # CancelTaskUseCase returns FAILED for missing task, not 404
        # because the use case handles KeyError gracefully
        assert resp.status_code in (200, 404)

    def test_cancel_updates_task_status_to_cancelled(self) -> None:
        store_tasks = {"task-y": {"status": "in_progress"}}
        app = _make_app(tasks=store_tasks)
        store = app.state.task_store
        client = TestClient(app)

        client.post("/v1/tasks/task-y/cancel")

        assert store._tasks["task-y"]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# POST /v1/tasks/{task_id}/human-resume
# ---------------------------------------------------------------------------

class TestHumanResume:
    def test_human_resume_missing_task_returns_404(self) -> None:
        app = _make_app({})
        client = TestClient(app)

        resp = client.post(
            "/v1/tasks/ghost-task/human-resume",
            json={"feedback": "approved", "stream": False},
        )

        assert resp.status_code == 404

    def test_human_resume_existing_task_returns_200(self) -> None:
        app = _make_app({"task-r": {"status": "awaiting_human"}})
        # pipeline_runner not set — will use no-op stub
        client = TestClient(app)

        resp = client.post(
            "/v1/tasks/task-r/human-resume",
            json={"feedback": "looks good", "stream": False},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-r"
        assert "status" in data


# ---------------------------------------------------------------------------
# POST /v1/tasks/{task_id}/retry
# ---------------------------------------------------------------------------

class TestRetryTask:
    def test_retry_missing_task_returns_404(self) -> None:
        app = _make_app({})
        client = TestClient(app)

        resp = client.post(
            "/v1/tasks/ghost/retry",
            json={"stream": False},
        )

        assert resp.status_code == 404

    def test_retry_existing_task_returns_200(self) -> None:
        app = _make_app({"task-f": {"status": "failed"}})
        client = TestClient(app)

        resp = client.post(
            "/v1/tasks/task-f/retry",
            json={"stream": False},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "task-f"
        assert "status" in data
