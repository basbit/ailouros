from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.App.integrations.domain.postmortem import Postmortem


def _make_pm(spec_id: str = "auth/login") -> Postmortem:
    return Postmortem(
        id="pm-test-001",
        spec_id=spec_id,
        agent="stub",
        failure_kind="verifier_error",
        summary="E501 line too long",
        findings_excerpt=("E501 line too long",),
        recovery_attempted="1 retry attempt(s) made",
        outcome="failed",
        recorded_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=(spec_id, "stub", "verifier_error"),
    )


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from backend.UI.REST.controllers.spec import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


_PATCH_RETRIEVE = "backend.App.integrations.application.postmortem_retrieval.retrieve_postmortems"
_PATCH_VECTOR = "backend.App.integrations.infrastructure.qdrant_client.get_vector_store"
_PATCH_EMBED = "backend.App.integrations.infrastructure.embedding_service.get_embedding_provider"


def test_list_postmortems_returns_200(client):
    pm = _make_pm()
    with (
        patch(_PATCH_RETRIEVE, return_value=(pm,)),
        patch(_PATCH_VECTOR, return_value=MagicMock()),
        patch(_PATCH_EMBED, return_value=None),
    ):
        response = client.get("/v1/postmortems?spec_id=auth/login&k=5")
    assert response.status_code == 200
    data = response.json()
    assert "postmortems" in data
    assert data["count"] == 1
    assert data["postmortems"][0]["spec_id"] == "auth/login"


def test_list_postmortems_empty_result(client):
    with (
        patch(_PATCH_RETRIEVE, return_value=()),
        patch(_PATCH_VECTOR, return_value=MagicMock()),
        patch(_PATCH_EMBED, return_value=None),
    ):
        response = client.get("/v1/postmortems")
    assert response.status_code == 200
    data = response.json()
    assert data["postmortems"] == []
    assert data["count"] == 0


def test_list_postmortems_passes_query_params(client):
    with (
        patch(_PATCH_RETRIEVE, return_value=()) as mock_retrieve,
        patch(_PATCH_VECTOR, return_value=MagicMock()),
        patch(_PATCH_EMBED, return_value=None),
    ):
        client.get(
            "/v1/postmortems",
            params={
                "spec_id": "auth/login",
                "agent": "mypy",
                "failure_kind": "verifier_error",
                "tag": "critical",
                "k": "3",
            },
        )

    called_query = mock_retrieve.call_args[0][0]
    assert called_query.spec_id == "auth/login"
    assert called_query.agent == "mypy"
    assert called_query.failure_kind == "verifier_error"
    assert called_query.tag == "critical"
    assert called_query.k == 3


def test_list_postmortems_serialised_fields(client):
    pm = _make_pm()
    with (
        patch(_PATCH_RETRIEVE, return_value=(pm,)),
        patch(_PATCH_VECTOR, return_value=MagicMock()),
        patch(_PATCH_EMBED, return_value=None),
    ):
        response = client.get("/v1/postmortems")

    item = response.json()["postmortems"][0]
    assert "id" in item
    assert "summary" in item
    assert "failure_kind" in item
    assert "outcome" in item
    assert "recorded_at" in item
    assert "tags" in item
    assert isinstance(item["findings_excerpt"], list)
