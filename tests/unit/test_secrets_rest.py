from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_SECRETS_PATH", str(tmp_path / "secrets.json"))
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def test_put_then_get_returns_name(client):
    response = client.put(
        "/v1/secrets",
        json={"name": "web_search.tavily", "value": "tvly-abc"},
    )
    assert response.status_code == 200
    listing = client.get("/v1/secrets")
    assert "web_search.tavily" in listing.json()["names"]


def test_put_rejects_blank_value(client):
    response = client.put(
        "/v1/secrets",
        json={"name": "web_search.tavily", "value": "  "},
    )
    assert response.status_code == 422


def test_delete_unknown_returns_404(client):
    response = client.delete("/v1/secrets/missing")
    assert response.status_code == 404


def test_delete_existing_returns_removed(client):
    client.put(
        "/v1/secrets",
        json={"name": "k", "value": "v"},
    )
    response = client.delete("/v1/secrets/k")
    assert response.status_code == 200
    assert response.json()["removed"] is True
