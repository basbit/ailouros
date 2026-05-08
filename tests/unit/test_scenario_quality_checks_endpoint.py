"""Тесты для GET /v1/tasks/{task_id}/scenario-quality-checks."""

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
    resp = client.get("/v1/tasks/none/scenario-quality-checks")
    assert resp.status_code == 404


def test_returns_persisted_results(client, artifacts_root):
    task_dir = artifacts_root / "abc"
    _write_pipeline(task_dir, {
        "scenario_id": "build_feature",
        "scenario_title": "Build Feature",
        "scenario_category": "development",
        "scenario_quality_checks": [
            {
                "id": "min_artifacts",
                "type": "artifact_count",
                "severity": "error",
                "blocking": True,
                "config": {"min": 3},
            },
        ],
        "scenario_quality_check_results": [
            {
                "id": "min_artifacts",
                "type": "artifact_count",
                "passed": True,
                "severity": "error",
                "blocking": True,
                "message": "3 of 5 expected artifacts present (min=3)",
            },
        ],
        "scenario_quality_check_summary": {
            "total": 1, "passed": 1, "failed": 0, "blocking_failed": [],
        },
    })

    resp = client.get("/v1/tasks/abc/scenario-quality-checks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "abc"
    assert data["scenario_id"] == "build_feature"
    assert len(data["specs"]) == 1
    assert len(data["results"]) == 1
    assert data["results"][0]["passed"] is True
    assert data["summary"]["passed"] == 1


def test_returns_empty_when_no_scenario(client, artifacts_root):
    task_dir = artifacts_root / "plain"
    _write_pipeline(task_dir, {"user_prompt": "x"})

    resp = client.get("/v1/tasks/plain/scenario-quality-checks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["specs"] == []
    assert data["results"] == []
    assert data["summary"]["total"] == 0


def test_recomputes_summary_when_missing(client, artifacts_root):
    task_dir = artifacts_root / "noSummary"
    _write_pipeline(task_dir, {
        "scenario_id": "x",
        "scenario_quality_check_results": [
            {"id": "a", "passed": True, "blocking": False},
            {"id": "b", "passed": False, "blocking": True},
            {"id": "c", "passed": False, "blocking": False},
        ],
    })
    resp = client.get("/v1/tasks/noSummary/scenario-quality-checks")
    data = resp.json()
    assert data["summary"]["total"] == 3
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failed"] == 2
    assert data["summary"]["blocking_failed"] == ["b"]
