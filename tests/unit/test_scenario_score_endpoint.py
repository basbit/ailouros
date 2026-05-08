"""Тесты для GET /v1/tasks/{task_id}/scenario-score."""

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
    resp = client.get("/v1/tasks/none/scenario-score")
    assert resp.status_code == 404


def test_perfect_run_returns_overall_one(client, artifacts_root):
    task_dir = artifacts_root / "abc"
    _write_pipeline(task_dir, {
        "scenario_id": "build_feature",
        "scenario_title": "Build Feature",
        "scenario_category": "development",
        "scenario_artifact_summary": {"present": 5, "missing": 0, "total": 5},
        "scenario_quality_check_summary": {"passed": 3, "total": 3, "failed": 0},
        "scenario_warnings": [],
    })
    resp = client.get("/v1/tasks/abc/scenario-score")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "abc"
    assert data["scenario_id"] == "build_feature"
    assert data["overall_score"] == 1.0
    assert data["artifact_score"] == 1.0
    assert data["quality_check_score"] == 1.0


def test_failing_run_lowers_overall(client, artifacts_root):
    task_dir = artifacts_root / "xyz"
    _write_pipeline(task_dir, {
        "scenario_id": "code_review",
        "scenario_artifact_summary": {"present": 1, "missing": 2, "total": 3},
        "scenario_quality_check_summary": {"passed": 0, "total": 2, "failed": 2},
        "scenario_warnings": ["a"],
    })
    resp = client.get("/v1/tasks/xyz/scenario-score")
    data = resp.json()
    assert data["artifact_score"] < 1.0
    assert data["quality_check_score"] == 0.0
    assert data["warnings_score"] < 1.0
    assert data["overall_score"] < 1.0


def test_no_scenario_run_still_returns_score(client, artifacts_root):
    task_dir = artifacts_root / "plain"
    _write_pipeline(task_dir, {"user_prompt": "x"})
    resp = client.get("/v1/tasks/plain/scenario-score")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] is None
    assert data["overall_score"] == 1.0


def test_breakdown_includes_weights(client, artifacts_root):
    task_dir = artifacts_root / "weights"
    _write_pipeline(task_dir, {
        "scenario_artifact_summary": {"present": 1, "total": 1},
        "scenario_quality_check_summary": {"passed": 1, "total": 1},
        "scenario_warnings": [],
    })
    resp = client.get("/v1/tasks/weights/scenario-score")
    data = resp.json()
    assert "weights" in data["breakdown"]
    assert "artifacts" in data["breakdown"]["weights"]
