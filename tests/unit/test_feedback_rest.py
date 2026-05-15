from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def _mock_infra(mock_store: MagicMock | None = None, mock_embed: MagicMock | None = None):
    store = mock_store or MagicMock()
    embed = mock_embed or MagicMock()
    store.upsert.return_value = None
    store.scroll.return_value = []
    store.search.return_value = []
    embed.embed.return_value = [[0.1, 0.2]]
    return store, embed


_QDRANT_PATH = "backend.App.integrations.infrastructure.qdrant_client.get_vector_store"
_EMBED_PATH = "backend.App.integrations.infrastructure.embedding_service.get_embedding_provider"


def test_post_feedback_accept(client):
    store, embed = _mock_infra()
    with (
        patch(_QDRANT_PATH, return_value=store),
        patch(_EMBED_PATH, return_value=embed),
    ):
        response = client.post(
            "/v1/codegen-feedback",
            json={
                "spec_id": "auth/login",
                "agent": "coder",
                "target_file": "src/auth/login.py",
                "verdict": "accept",
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "recorded_at" in data
    store.upsert.assert_called_once()


def test_post_feedback_reject_with_reason(client):
    store, embed = _mock_infra()
    with (
        patch(_QDRANT_PATH, return_value=store),
        patch(_EMBED_PATH, return_value=embed),
    ):
        response = client.post(
            "/v1/codegen-feedback",
            json={
                "spec_id": "auth/login",
                "agent": "coder",
                "target_file": "src/auth/login.py",
                "verdict": "reject",
                "reason": "wrong logic",
            },
        )
    assert response.status_code == 200


def test_post_feedback_edit_with_diff(client):
    store, embed = _mock_infra()
    with (
        patch(_QDRANT_PATH, return_value=store),
        patch(_EMBED_PATH, return_value=embed),
    ):
        response = client.post(
            "/v1/codegen-feedback",
            json={
                "spec_id": "auth/login",
                "agent": "coder",
                "target_file": "src/auth/login.py",
                "verdict": "edit",
                "user_edit_diff": "@@ -1 +1 @@\n-old\n+new",
            },
        )
    assert response.status_code == 200


def test_post_feedback_invalid_verdict_422(client):
    response = client.post(
        "/v1/codegen-feedback",
        json={
            "spec_id": "s",
            "agent": "a",
            "target_file": "f.py",
            "verdict": "maybe",
        },
    )
    assert response.status_code == 422


def test_post_feedback_missing_spec_id_422(client):
    response = client.post(
        "/v1/codegen-feedback",
        json={
            "agent": "a",
            "target_file": "f.py",
            "verdict": "accept",
        },
    )
    assert response.status_code == 422


def test_get_feedback_empty(client):
    store, embed = _mock_infra()
    with (
        patch(_QDRANT_PATH, return_value=store),
        patch(_EMBED_PATH, return_value=embed),
    ):
        response = client.get(
            "/v1/codegen-feedback",
            params={"spec_id": "auth/login", "target_file": "src/auth/login.py"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 0
    assert data["items"] == []


def test_get_feedback_missing_spec_id_422(client):
    response = client.get(
        "/v1/codegen-feedback",
        params={"target_file": "f.py"},
    )
    assert response.status_code == 422


def test_get_feedback_missing_target_file_422(client):
    response = client.get(
        "/v1/codegen-feedback",
        params={"spec_id": "s"},
    )
    assert response.status_code == 422
