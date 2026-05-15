from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_SPEC_BODY = (
    "\n## Purpose\n\nAuthenticate users.\n\n"
    "## Public Contract\n\ndef login(user: str, pw: str) -> bool: ...\n\n"
    "## Behaviour\n\nVerify credentials.\n\n"
    "## Examples\n\n```python\nlogin('a','b') -> True\n```\n"
)

_TARGET = "src/auth/login.py"


@pytest.fixture()
def rest_client():
    from backend.App.shared.infrastructure.rest.app import app
    return TestClient(app)


def _write_spec(workspace_root: Path) -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(_TARGET,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = workspace_root / ".swarm" / "specs" / "auth"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


def test_differential_endpoint_returns_200(rest_client: TestClient, tmp_path: Path) -> None:
    _write_spec(tmp_path)
    os.environ["SWARM_SPEC_CODEGEN_STUB"] = "1"
    try:
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/generate/differential",
            json={
                "workspace_root": str(tmp_path),
                "primary_model": "stub-a",
                "alternative_model": "stub-b",
            },
        )
    finally:
        del os.environ["SWARM_SPEC_CODEGEN_STUB"]
    assert response.status_code == 200


def test_differential_endpoint_response_shape(rest_client: TestClient, tmp_path: Path) -> None:
    _write_spec(tmp_path)
    os.environ["SWARM_SPEC_CODEGEN_STUB"] = "1"
    try:
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/generate/differential",
            json={
                "workspace_root": str(tmp_path),
                "primary_model": "stub-a",
                "alternative_model": "stub-b",
            },
        )
    finally:
        del os.environ["SWARM_SPEC_CODEGEN_STUB"]
    payload = response.json()
    assert "spec_id" in payload
    assert "agreement_ratio" in payload
    assert "findings" in payload
    assert "primary_outcome" in payload
    assert "alternative_outcome" in payload


def test_differential_endpoint_model_names_in_response(
    rest_client: TestClient, tmp_path: Path
) -> None:
    _write_spec(tmp_path)
    os.environ["SWARM_SPEC_CODEGEN_STUB"] = "1"
    try:
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/generate/differential",
            json={
                "workspace_root": str(tmp_path),
                "primary_model": "gpt-4o",
                "alternative_model": "claude-opus",
            },
        )
    finally:
        del os.environ["SWARM_SPEC_CODEGEN_STUB"]
    payload = response.json()
    assert payload["primary_model"] == "gpt-4o"
    assert payload["alternative_model"] == "claude-opus"


def test_differential_endpoint_missing_workspace_returns_400(
    rest_client: TestClient,
) -> None:
    response = rest_client.post(
        "/v1/spec/auth%2Flogin/generate/differential",
        json={
            "workspace_root": "",
            "primary_model": "a",
            "alternative_model": "b",
        },
    )
    assert response.status_code == 400


def test_differential_endpoint_nonexistent_spec_returns_400(
    rest_client: TestClient, tmp_path: Path
) -> None:
    os.environ["SWARM_SPEC_CODEGEN_STUB"] = "1"
    try:
        response = rest_client.post(
            "/v1/spec/no%2Fsuch/generate/differential",
            json={
                "workspace_root": str(tmp_path),
                "primary_model": "a",
                "alternative_model": "b",
            },
        )
    finally:
        del os.environ["SWARM_SPEC_CODEGEN_STUB"]
    assert response.status_code == 400
