"""TEST-03: Schedule API lifecycle tests."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

import backend.UI.REST.task_instance as _task_instance_mod
import backend.UI.REST.app as orchestrator_api


@pytest.fixture(autouse=True)
def _isolated_artifacts(tmp_path, monkeypatch):
    d = (tmp_path / "artifacts").resolve()
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(d))
    monkeypatch.setattr(_task_instance_mod, "ARTIFACTS_ROOT", d)


def test_schedule_create_and_list():
    """POST /v1/schedule creates a job; GET /v1/schedule lists it."""
    client = TestClient(orchestrator_api.app)

    r = client.post(
        "/v1/schedule",
        json={
            "prompt": "daily report",
            "interval_seconds": 86400,
            "delay_seconds": 99999,
            "enabled": False,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    job_id = body["job_id"]
    assert job_id

    r2 = client.get("/v1/schedule")
    assert r2.status_code == 200
    ids = {j["id"] for j in r2.json()["jobs"]}
    assert job_id in ids

    # Cleanup
    client.delete(f"/v1/schedule/{job_id}")


def test_schedule_update_fields():
    """PATCH /v1/schedule/{id} updates allowed fields."""
    client = TestClient(orchestrator_api.app)

    r = client.post(
        "/v1/schedule",
        json={"prompt": "test", "interval_seconds": 3600, "enabled": False},
    )
    job_id = r.json()["job_id"]

    try:
        r2 = client.patch(f"/v1/schedule/{job_id}", json={"interval_seconds": 7200, "name": "renamed"})
        assert r2.status_code == 200
        job = r2.json()["job"]
        assert job["interval_seconds"] == 7200
        assert job["name"] == "renamed"
    finally:
        client.delete(f"/v1/schedule/{job_id}")


def test_schedule_update_rejects_zero_interval():
    """PATCH rejects interval_seconds=0 with HTTP 422."""
    client = TestClient(orchestrator_api.app)

    r = client.post(
        "/v1/schedule",
        json={"prompt": "x", "interval_seconds": 3600, "enabled": False},
    )
    job_id = r.json()["job_id"]

    try:
        r2 = client.patch(f"/v1/schedule/{job_id}", json={"interval_seconds": 0})
        assert r2.status_code == 422

        r3 = client.patch(f"/v1/schedule/{job_id}", json={"interval_seconds": -1})
        assert r3.status_code == 422
    finally:
        client.delete(f"/v1/schedule/{job_id}")


def test_schedule_delete():
    """DELETE removes the job; subsequent PATCH returns 404."""
    client = TestClient(orchestrator_api.app)

    r = client.post(
        "/v1/schedule",
        json={"prompt": "gone", "interval_seconds": 3600, "enabled": False},
    )
    job_id = r.json()["job_id"]

    r2 = client.delete(f"/v1/schedule/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["deleted"] == job_id

    r3 = client.patch(f"/v1/schedule/{job_id}", json={})
    assert r3.status_code == 404

    r4 = client.delete(f"/v1/schedule/{job_id}")
    assert r4.status_code == 404


def test_schedule_fire_calls_pipeline(monkeypatch):
    """_schedule_fire runs run_pipeline in a background thread for an enabled job."""
    called = []

    def fake_pipeline(
        prompt,
        agent_config=None,
        pipeline_steps=None,
        workspace_root="",
        workspace_apply_writes=False,
        task_id="",
        **_,
    ):
        called.append({"prompt": prompt, "task_id": task_id})
        return {}

    monkeypatch.setattr(
        "backend.App.orchestration.application.pipeline_graph.run_pipeline",
        fake_pipeline,
    )

    from backend.UI.REST.controllers.schedules import _schedule_fire, _schedule_lock, _schedule_store

    job_id = str(uuid.uuid4())
    with _schedule_lock:
        _schedule_store[job_id] = {
            "id": job_id,
            "prompt": "fire test",
            "interval_seconds": 0,  # no re-schedule
            "agent_config": {},
            "pipeline_steps": None,
            "workspace_root": "",
            "workspace_write": False,
            "enabled": True,
        }

    try:
        _schedule_fire(job_id)
        # The pipeline runs in a daemon thread; give it a moment
        deadline = time.time() + 3.0
        while not called and time.time() < deadline:
            time.sleep(0.05)
        assert called, "run_pipeline was not called"
        assert called[0]["prompt"] == "fire test"
    finally:
        with _schedule_lock:
            _schedule_store.pop(job_id, None)


def test_schedule_disabled_job_does_not_fire(monkeypatch):
    """_schedule_fire is a no-op for disabled jobs."""
    called = []
    monkeypatch.setattr(
        "backend.App.orchestration.application.pipeline_graph.run_pipeline",
        lambda *a, **k: called.append(1) or {},
    )

    from backend.UI.REST.controllers.schedules import _schedule_fire, _schedule_lock, _schedule_store

    job_id = str(uuid.uuid4())
    with _schedule_lock:
        _schedule_store[job_id] = {
            "id": job_id,
            "prompt": "should not run",
            "interval_seconds": 0,
            "agent_config": {},
            "pipeline_steps": None,
            "workspace_root": "",
            "workspace_write": False,
            "enabled": False,
        }

    try:
        _schedule_fire(job_id)
        time.sleep(0.1)
        assert not called, "disabled job should not fire"
    finally:
        with _schedule_lock:
            _schedule_store.pop(job_id, None)
