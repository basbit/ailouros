import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.App.orchestration.application.scenarios.registry import (
    default_scenario_registry,
)
from backend.App.orchestration.domain.scenario_estimate import (
    ScenarioEstimate,
    StepEstimate,
    compute_scenario_estimate,
)
from backend.UI.REST.controllers.scenarios import router


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


def test_aggregates_inline_object_steps():
    payload = {
        "id": "demo",
        "pipeline_steps": [
            {"id": "a", "estimated_duration_sec": 60, "essential": True},
            {"id": "b", "estimated_duration_sec": 120, "essential": False},
        ],
    }
    estimate = compute_scenario_estimate(payload)
    assert isinstance(estimate, ScenarioEstimate)
    assert estimate.scenario_id == "demo"
    assert estimate.total_seconds == 180
    assert estimate.essential_seconds == 60
    assert len(estimate.steps) == 2
    assert estimate.steps[0] == StepEstimate("a", 60, True)
    assert estimate.steps[1] == StepEstimate("b", 120, False)


def test_string_steps_default_to_essential_and_none_duration():
    payload = {
        "id": "demo",
        "pipeline_steps": ["a", "b"],
    }
    estimate = compute_scenario_estimate(payload)
    assert estimate.total_seconds is None
    assert estimate.essential_seconds is None
    assert all(s.essential for s in estimate.steps)
    assert all(s.estimated_duration_sec is None for s in estimate.steps)


def test_step_estimates_parallel_block_applies_to_string_steps():
    payload = {
        "id": "demo",
        "pipeline_steps": ["a", "b"],
        "step_estimates": [
            {"step_id": "a", "estimated_duration_sec": 60, "essential": True},
            {"step_id": "b", "estimated_duration_sec": 30, "essential": False},
        ],
    }
    estimate = compute_scenario_estimate(payload)
    assert estimate.total_seconds == 90
    assert estimate.essential_seconds == 60


def test_partial_duration_returns_none_total_explicitly():
    payload = {
        "id": "demo",
        "pipeline_steps": [
            {"id": "a", "estimated_duration_sec": 60},
            {"id": "b"},
        ],
    }
    estimate = compute_scenario_estimate(payload)
    assert estimate.total_seconds is None
    assert estimate.essential_seconds is None


def test_only_essential_aggregates_when_non_essential_missing_duration():
    payload = {
        "id": "demo",
        "pipeline_steps": [
            {"id": "a", "estimated_duration_sec": 60, "essential": True},
            {"id": "b", "essential": False},
        ],
    }
    estimate = compute_scenario_estimate(payload)
    assert estimate.total_seconds is None
    assert estimate.essential_seconds == 60


def test_rejects_negative_duration():
    payload = {
        "id": "demo",
        "pipeline_steps": [{"id": "a", "estimated_duration_sec": -1}],
    }
    with pytest.raises(ValueError):
        compute_scenario_estimate(payload)


def test_rejects_non_int_duration():
    payload = {
        "id": "demo",
        "pipeline_steps": [{"id": "a", "estimated_duration_sec": "60"}],
    }
    with pytest.raises(ValueError):
        compute_scenario_estimate(payload)


def test_rejects_non_bool_essential():
    payload = {
        "id": "demo",
        "pipeline_steps": [{"id": "a", "essential": "yes"}],
    }
    with pytest.raises(ValueError):
        compute_scenario_estimate(payload)


def test_rejects_missing_pipeline_steps():
    with pytest.raises(ValueError):
        compute_scenario_estimate({"id": "demo"})


def test_rejects_missing_id():
    with pytest.raises(ValueError):
        compute_scenario_estimate({"pipeline_steps": ["a"]})


def test_to_dict_shape():
    payload = {
        "id": "demo",
        "pipeline_steps": [
            {"id": "a", "estimated_duration_sec": 10, "essential": True},
        ],
    }
    data = compute_scenario_estimate(payload).to_dict()
    assert data == {
        "scenario_id": "demo",
        "steps": [
            {"step_id": "a", "estimated_duration_sec": 10, "essential": True},
        ],
        "total_seconds": 10,
        "essential_seconds": 10,
    }


def test_rest_endpoint_returns_estimate_for_spec_driven_feature(client):
    resp = client.get("/v1/scenarios/spec_driven_feature/estimate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "spec_driven_feature"
    assert data["total_seconds"] is not None
    assert data["essential_seconds"] is not None
    assert data["essential_seconds"] <= data["total_seconds"]
    ids = [s["step_id"] for s in data["steps"]]
    assert "clarify_input" in ids


def test_rest_endpoint_returns_estimate_for_build_feature(client):
    resp = client.get("/v1/scenarios/build_feature/estimate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "build_feature"
    assert data["total_seconds"] is not None
    non_essential = [
        s for s in data["steps"] if not s["essential"]
    ]
    assert non_essential, "build_feature should have at least one non-essential step"


def test_rest_endpoint_404_for_unknown_scenario(client):
    resp = client.get("/v1/scenarios/does_not_exist/estimate")
    assert resp.status_code == 404
