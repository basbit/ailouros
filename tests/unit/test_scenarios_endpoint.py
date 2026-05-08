"""Тесты для /v1/scenarios REST endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.UI.REST.controllers.scenarios import router
from backend.App.orchestration.application.scenarios.registry import default_scenario_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    default_scenario_registry.cache_clear()
    yield
    default_scenario_registry.cache_clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_list_scenarios_returns_bundled(client):
    resp = client.get("/v1/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    ids = {s["id"] for s in data["scenarios"]}
    assert "build_feature" in ids
    assert "code_review" in ids
    assert "research_brief" in ids


def test_list_scenarios_sorted_by_category_title(client):
    resp = client.get("/v1/scenarios")
    data = resp.json()
    scenarios = data["scenarios"]
    keys = [(s["category"], s["title"]) for s in scenarios]
    assert keys == sorted(keys)


def test_get_scenario_build_feature(client):
    resp = client.get("/v1/scenarios/build_feature")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "build_feature"
    assert "pipeline_steps" in data
    assert "default_gates" in data
    assert "expected_artifacts" in data
    assert "required_tools" in data
    assert "recommended_models" in data
    assert "workspace_write_default" in data
    assert "tags" in data


def test_get_scenario_not_found(client):
    resp = client.get("/v1/scenarios/does_not_exist")
    assert resp.status_code == 404


def test_preview_research_brief_warns_web_search(client):
    resp = client.post("/v1/scenarios/preview", json={"scenario_id": "research_brief"})
    assert resp.status_code == 200
    data = resp.json()
    warnings = data.get("warnings", [])
    assert any("web_search" in w for w in warnings)


def test_preview_with_pipeline_steps_override(client):
    resp = client.post(
        "/v1/scenarios/preview",
        json={"scenario_id": "build_feature", "pipeline_steps": ["pm"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pipeline_steps"] == ["pm"]


def test_preview_with_skip_gates_removes_gate(client):
    resp = client.post(
        "/v1/scenarios/preview",
        json={"scenario_id": "build_feature", "skip_gates": ["human_qa"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "human_qa" not in data["pipeline_steps"]
    assert "human_qa" not in data["default_gates"]
    assert data["skipped_gates"] == ["human_qa"]


def test_preview_with_model_profile_marks_applied(client):
    resp = client.post(
        "/v1/scenarios/preview",
        json={
            "scenario_id": "code_review",
            "model_profile": {"analyze_code": "qwen2.5-coder"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_profile_applied"] == {"analyze_code": "qwen2.5-coder"}
    assert data["agent_config"]["analyze_code"]["model"] == "qwen2.5-coder"
