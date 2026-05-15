from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_BODY = (
    "\n## Purpose\n\nTest.\n\n"
    "## Public Contract\n\ndef foo() -> None: ...\n\n"
    "## Behaviour\n\nDoes nothing.\n\n"
    "## Examples\n\nfoo()\n"
)
_TARGET = "src/auth/login.py"


def _write_spec(workspace_root: Path) -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(_TARGET,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_BODY, sections=())
    spec_dir = workspace_root / ".swarm" / "specs" / "auth"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


@pytest.fixture()
def client():
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def test_generate_with_stub_env(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    _write_spec(tmp_path)
    response = client.post(
        "/v1/spec/auth/login/generate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec_id"] == "auth/login"
    assert _TARGET in payload["written_files"]
    assert len(payload["sidecar_paths"]) == 1


def test_generate_writes_actual_file(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    _write_spec(tmp_path)
    client.post(
        "/v1/spec/auth/login/generate",
        json={"workspace_root": str(tmp_path)},
    )
    assert (tmp_path / _TARGET).is_file()


def test_generate_no_client_no_stub_returns_400(client, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SWARM_SPEC_CODEGEN_STUB", raising=False)
    _write_spec(tmp_path)
    response = client.post(
        "/v1/spec/auth/login/generate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 400
    assert "SWARM_SPEC_CODEGEN_STUB" in response.json()["detail"]


def test_generate_unknown_spec_returns_400(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    (tmp_path / ".swarm" / "specs").mkdir(parents=True)
    response = client.post(
        "/v1/spec/no/such/spec/generate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 400


def test_generate_accepts_model_and_seed(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    _write_spec(tmp_path)
    response = client.post(
        "/v1/spec/auth/login/generate",
        json={"workspace_root": str(tmp_path), "model_name": "gpt-4", "seed": 42},
    )
    assert response.status_code == 200


def test_drift_empty_workspace_returns_ok(client, tmp_path: Path):
    (tmp_path / ".swarm" / "specs").mkdir(parents=True)
    response = client.get("/v1/spec/drift", params={"workspace_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert payload["stale_code"] == []
    assert payload["stale_specs"] == []
    assert payload["aged_keep_regions"] == []


def test_drift_detects_stale_code(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_SPEC_CODEGEN_STUB", "1")
    _write_spec(tmp_path)
    client.post(
        "/v1/spec/auth/login/generate",
        json={"workspace_root": str(tmp_path)},
    )

    spec_dir = tmp_path / ".swarm" / "specs" / "auth"
    login_md = spec_dir / "login.md"
    current = login_md.read_text(encoding="utf-8")
    login_md.write_text(
        current.replace("Does nothing.", "Does something different now."),
        encoding="utf-8",
    )

    response = client.get("/v1/spec/drift", params={"workspace_root": str(tmp_path)})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["stale_code"]) == 1
    assert payload["stale_code"][0]["spec_id"] == "auth/login"


def test_drift_missing_workspace_returns_400(client):
    response = client.get("/v1/spec/drift", params={"workspace_root": ""})
    assert response.status_code == 400
