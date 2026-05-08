"""Тесты для POST /v1/scenarios/validate."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.UI.REST.controllers.scenarios import router
from backend.App.orchestration.application.scenarios.registry import (
    default_scenario_registry,
)


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


def _valid_payload() -> dict:
    return {
        "id": "custom_demo",
        "title": "Custom Demo",
        "category": "development",
        "description": "Demo scenario for validation tests.",
        "pipeline_steps": ["pm", "dev"],
        "default_gates": [],
        "expected_artifacts": [],
        "required_tools": [],
        "workspace_write_default": False,
        "recommended_models": {},
        "tags": [],
        "quality_checks": [],
        "inputs": [],
    }


def test_validate_accepts_valid_payload(client):
    resp = client.post("/v1/scenarios/validate", json=_valid_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["id"] == "custom_demo"
    assert body["summary"]["pipeline_steps"] == ["pm", "dev"]


def test_validate_rejects_missing_required_field(client):
    payload = _valid_payload()
    del payload["title"]
    resp = client.post("/v1/scenarios/validate", json=payload)
    assert resp.status_code == 422
    assert "title" in resp.json()["detail"]


def test_validate_rejects_unknown_step_id(client):
    payload = _valid_payload()
    payload["pipeline_steps"] = ["pm", "totally_unknown_step"]
    resp = client.post("/v1/scenarios/validate", json=payload)
    assert resp.status_code == 422
    assert "totally_unknown_step" in resp.json()["detail"]
