from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def test_init_creates_specs(client, tmp_path: Path):
    response = client.post(
        "/v1/spec/init",
        json={
            "workspace_root": str(tmp_path),
            "project_title": "Demo",
            "project_summary": "test workspace",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "_project" in payload["created_spec_ids"]
    assert payload["bootstrapped"] is True


def test_list_after_init(client, tmp_path: Path):
    client.post(
        "/v1/spec/init",
        json={"workspace_root": str(tmp_path)},
    )
    response = client.get(
        "/v1/spec/list",
        params={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "_project" in payload["spec_ids"]
    assert "_schema" in payload["spec_ids"]


def test_show_returns_document(client, tmp_path: Path):
    client.post(
        "/v1/spec/init",
        json={
            "workspace_root": str(tmp_path),
            "initial_module_spec_id": "auth/password",
        },
    )
    response = client.get(
        "/v1/spec/show",
        params={"workspace_root": str(tmp_path), "spec_id": "auth/password"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["spec"]["spec_id"] == "auth/password"
    assert body["dependencies"] == []
    assert body["dependants"] == []


def test_show_missing_returns_404(client, tmp_path: Path):
    client.post(
        "/v1/spec/init",
        json={"workspace_root": str(tmp_path)},
    )
    response = client.get(
        "/v1/spec/show",
        params={"workspace_root": str(tmp_path), "spec_id": "no/such/thing"},
    )
    assert response.status_code == 404


def test_put_spec_round_trip(client, tmp_path: Path):
    client.post(
        "/v1/spec/init",
        json={"workspace_root": str(tmp_path)},
    )
    body = (
        "\n## Purpose\n\nManual test.\n\n"
        "## Public Contract\n\nIt does the thing.\n\n"
        "## Behaviour\n\nWhen invoked, it returns OK.\n"
    )
    response = client.put(
        "/v1/spec/test/manual",
        json={
            "workspace_root": str(tmp_path),
            "body": body,
            "frontmatter": {
                "spec_id": "test/manual",
                "version": 1,
                "status": "draft",
                "privacy": "internal",
                "title": "Manual test",
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["spec_id"] == "test/manual"
    show = client.get(
        "/v1/spec/show",
        params={"workspace_root": str(tmp_path), "spec_id": "test/manual"},
    ).json()
    assert "Manual test" in show["spec"]["title"]
