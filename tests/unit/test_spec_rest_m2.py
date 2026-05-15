from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def _bootstrap(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/v1/spec/init",
        json={
            "workspace_root": str(tmp_path),
            "initial_module_spec_id": "auth/password",
        },
    )
    assert response.status_code == 200


def test_validate_returns_findings(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.post(
        "/v1/spec/auth/password/validate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec_id"] == "auth/password"
    assert "ok" in payload
    assert "findings" in payload


def test_validate_unknown_spec_returns_404(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.post(
        "/v1/spec/no/such/spec/validate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 404


def test_graph_endpoint_returns_nodes_and_edges(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.get(
        "/v1/spec/graph",
        params={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "nodes" in payload
    assert "edges" in payload
    spec_ids = {node["id"] for node in payload["nodes"] if node["kind"] == "spec"}
    assert "_project" in spec_ids


def test_graph_persist_writes_file(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.get(
        "/v1/spec/graph",
        params={"workspace_root": str(tmp_path), "persist": "true"},
    )
    assert response.status_code == 200
    persisted_path = response.json().get("persisted_path")
    assert persisted_path is not None
    assert Path(persisted_path).is_file()


def test_ancestors_endpoint(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.get(
        "/v1/spec/auth/password/ancestors",
        params={"workspace_root": str(tmp_path), "depth": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec_id"] == "auth/password"
    assert isinstance(payload["ancestors"], list)


def test_orphans_endpoint(client, tmp_path: Path):
    _bootstrap(client, tmp_path)
    response = client.get(
        "/v1/spec/orphans",
        params={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "orphans" in payload


def test_extract_returns_spec_payload(client, tmp_path: Path):
    code_path = tmp_path / "src" / "demo.py"
    code_path.parent.mkdir(parents=True)
    code_path.write_text(
        '"""Demo module."""\n\ndef greet(name: str) -> str:\n    return name\n',
        encoding="utf-8",
    )
    response = client.post(
        "/v1/spec/extract",
        json={
            "workspace_root": str(tmp_path),
            "code_path": str(code_path),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec"]["spec_id"] == "src/demo"
    assert "greet" in payload["spec"]["body"]
    assert payload["saved"] is False


def test_extract_save_persists_to_filesystem(client, tmp_path: Path):
    code_path = tmp_path / "src" / "demo.py"
    code_path.parent.mkdir(parents=True)
    code_path.write_text(
        '"""Demo module."""\n\ndef greet(name: str) -> str:\n    return name\n',
        encoding="utf-8",
    )
    response = client.post(
        "/v1/spec/extract",
        json={
            "workspace_root": str(tmp_path),
            "code_path": str(code_path),
            "save": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert (tmp_path / ".swarm" / "specs" / "src" / "demo.md").is_file()
