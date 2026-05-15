from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)

_SPEC_BODY = (
    "\n## Purpose\n\nAuth users.\n\n"
    "## Public Contract\n\ndef login() -> bool: ...\n\n"
    "## Behaviour\n\nVerify.\n\n"
    "## Examples\n\n```python\nlogin() -> True\n```\n"
)

_TARGET = "src/auth/login.py"


@pytest.fixture()
def rest_client():
    from fastapi import FastAPI

    from backend.UI.REST.controllers.spec import router as spec_router

    app = FastAPI()
    app.include_router(spec_router)
    return TestClient(app)


def _write_spec(workspace_root: Path, targets: tuple[str, ...] = (_TARGET,)) -> None:
    frontmatter = SpecFrontmatter(
        spec_id="auth/login",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=targets,
    )
    document = SpecDocument(frontmatter=frontmatter, body=_SPEC_BODY, sections=())
    specs_dir = workspace_root / ".swarm" / "specs" / "auth"
    specs_dir.mkdir(parents=True, exist_ok=True)
    (specs_dir / "login.md").write_text(render_spec(document), encoding="utf-8")


def _run_result(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _results_json(target: str, total: int, killed: int, survived: int) -> str:
    return json.dumps(
        {"files": {target: {"total": total, "killed": killed, "survived": survived}}}
    )


def test_mutate_endpoint_returns_200(rest_client: TestClient, tmp_path: Path) -> None:
    _write_spec(tmp_path)
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(_TARGET, total=10, killed=7, survived=3), returncode=0),
    ]
    with patch(
        "backend.App.spec.infrastructure.verifiers.mutation_verifier.importlib.util.find_spec",
        return_value=object(),
    ), patch("subprocess.run", side_effect=side_effect):
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/mutate",
            json={"workspace_root": str(tmp_path), "threshold": 0.6},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["spec_id"] == "auth/login"
    assert payload["threshold"] == 0.6
    assert len(payload["stats"]) == 1
    stat = payload["stats"][0]
    assert stat["target_path"] == _TARGET
    assert stat["mutants_total"] == 10
    assert stat["mutants_killed"] == 7
    assert stat["score"] == pytest.approx(0.7)
    assert stat["below_threshold"] is False


def test_mutate_endpoint_below_threshold(rest_client: TestClient, tmp_path: Path) -> None:
    _write_spec(tmp_path)
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(_TARGET, total=10, killed=3, survived=7), returncode=0),
    ]
    with patch(
        "backend.App.spec.infrastructure.verifiers.mutation_verifier.importlib.util.find_spec",
        return_value=object(),
    ), patch("subprocess.run", side_effect=side_effect):
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/mutate",
            json={"workspace_root": str(tmp_path), "threshold": 0.6},
        )
    assert response.status_code == 200
    assert response.json()["stats"][0]["below_threshold"] is True


def test_mutate_endpoint_missing_workspace_returns_400(rest_client: TestClient) -> None:
    response = rest_client.post(
        "/v1/spec/auth%2Flogin/mutate",
        json={"workspace_root": ""},
    )
    assert response.status_code == 400


def test_mutate_endpoint_no_codegen_targets_returns_400(
    rest_client: TestClient, tmp_path: Path
) -> None:
    _write_spec(tmp_path, targets=())
    response = rest_client.post(
        "/v1/spec/auth%2Flogin/mutate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code == 400
    assert "codegen_targets" in response.json()["detail"]


def test_mutate_endpoint_missing_mutmut_returns_400(
    rest_client: TestClient, tmp_path: Path
) -> None:
    _write_spec(tmp_path)
    with patch(
        "backend.App.spec.infrastructure.verifiers.mutation_verifier.importlib.util.find_spec",
        return_value=None,
    ):
        response = rest_client.post(
            "/v1/spec/auth%2Flogin/mutate",
            json={"workspace_root": str(tmp_path)},
        )
    assert response.status_code == 400
    assert "mutmut is not installed" in response.json()["detail"]


def test_mutate_endpoint_nonexistent_spec_returns_404(
    rest_client: TestClient, tmp_path: Path
) -> None:
    response = rest_client.post(
        "/v1/spec/no%2Fsuch/mutate",
        json={"workspace_root": str(tmp_path)},
    )
    assert response.status_code in (400, 404)
