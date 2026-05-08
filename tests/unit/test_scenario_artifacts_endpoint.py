"""Тесты для GET /v1/tasks/{task_id}/scenario-artifacts."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.UI.REST.controllers.tasks import router
import backend.App.shared.infrastructure.rest.task_instance as task_instance


@pytest.fixture
def artifacts_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(task_instance, "ARTIFACTS_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _write_pipeline(task_dir: Path, payload: dict) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "pipeline.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_returns_404_when_pipeline_json_missing(client, artifacts_root):
    resp = client.get("/v1/tasks/none/scenario-artifacts")
    assert resp.status_code == 404


def test_uses_persisted_status_when_present(client, artifacts_root):
    task_dir = artifacts_root / "abc"
    (task_dir / "agents").mkdir(parents=True)
    (task_dir / "pipeline.json").touch()
    (task_dir / "agents" / "pm.txt").write_text("x", encoding="utf-8")
    _write_pipeline(task_dir, {
        "scenario_id": "build_feature",
        "scenario_title": "Build Feature",
        "scenario_category": "development",
        "scenario_expected_artifacts": ["pipeline.json", "agents/pm.txt", "missing.txt"],
        "scenario_artifact_status": [
            {"path": "pipeline.json", "present": True, "size": 0, "mtime": 1.0},
            {"path": "agents/pm.txt", "present": True, "size": 1, "mtime": 1.0},
            {"path": "missing.txt", "present": False, "size": None, "mtime": None},
        ],
        "scenario_artifact_summary": {"present": 2, "missing": 1, "total": 3},
    })

    resp = client.get("/v1/tasks/abc/scenario-artifacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "abc"
    assert data["scenario_id"] == "build_feature"
    assert data["scenario_title"] == "Build Feature"
    assert data["scenario_category"] == "development"
    assert data["expected_artifacts"] == [
        "pipeline.json", "agents/pm.txt", "missing.txt",
    ]
    assert data["summary"] == {"present": 2, "missing": 1, "total": 3}
    assert len(data["status"]) == 3
    assert data["status"][0]["url"] == "/artifacts/abc/pipeline.json"
    assert data["status"][1]["url"] == "/artifacts/abc/agents/pm.txt"


def test_recomputes_when_status_missing(client, artifacts_root):
    task_dir = artifacts_root / "xyz"
    task_dir.mkdir()
    (task_dir / "pipeline.json").touch()
    _write_pipeline(task_dir, {
        "scenario_id": "code_review",
        "scenario_expected_artifacts": ["pipeline.json", "agents/x.txt"],
    })

    resp = client.get("/v1/tasks/xyz/scenario-artifacts")
    assert resp.status_code == 200
    data = resp.json()
    paths = {entry["path"]: entry for entry in data["status"]}
    assert paths["pipeline.json"]["present"] is True
    assert paths["agents/x.txt"]["present"] is False
    assert data["summary"]["total"] == 2
    assert data["summary"]["present"] == 1
    assert data["summary"]["missing"] == 1


def test_no_scenario_returns_empty_status(client, artifacts_root):
    task_dir = artifacts_root / "plain"
    task_dir.mkdir()
    _write_pipeline(task_dir, {
        "user_prompt": "x",
    })

    resp = client.get("/v1/tasks/plain/scenario-artifacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] is None
    assert data["expected_artifacts"] == []
    assert data["status"] == []
    assert data["summary"]["total"] == 0
