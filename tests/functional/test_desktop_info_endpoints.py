from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.App.shared.infrastructure.rest.app as orchestrator_api
from backend.App.shared.application import desktop_mode


@pytest.fixture
def _client_without_desktop(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv(desktop_mode.DESKTOP_FLAG_ENV, raising=False)
    monkeypatch.delenv(desktop_mode.WORKSPACES_DIR_ENV, raising=False)
    return TestClient(orchestrator_api.app)


@pytest.fixture
def _client_with_desktop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> TestClient:
    monkeypatch.setenv(desktop_mode.DESKTOP_FLAG_ENV, "1")
    monkeypatch.setenv(desktop_mode.WORKSPACES_DIR_ENV, str(tmp_path))
    return TestClient(orchestrator_api.app)


def test_info_reports_off_when_desktop_disabled(
    _client_without_desktop: TestClient,
) -> None:
    response = _client_without_desktop.get("/v1/desktop/info")
    assert response.status_code == 200
    assert response.json() == {"is_desktop": False, "workspaces_dir": None}


def test_info_returns_workspaces_dir(
    _client_with_desktop: TestClient, tmp_path: Path
) -> None:
    response = _client_with_desktop.get("/v1/desktop/info")
    assert response.status_code == 200
    body = response.json()
    assert body["is_desktop"] is True
    assert body["workspaces_dir"] == str(tmp_path.resolve())


def test_init_rejects_when_desktop_off(
    _client_without_desktop: TestClient,
) -> None:
    response = _client_without_desktop.post(
        "/v1/desktop/projects/init", json={"project_id": "game"}
    )
    assert response.status_code == 400
    assert "desktop mode" in response.json()["detail"]


def test_init_creates_workspace_dir(
    _client_with_desktop: TestClient, tmp_path: Path
) -> None:
    response = _client_with_desktop.post(
        "/v1/desktop/projects/init", json={"project_id": "game"}
    )
    assert response.status_code == 200
    expected = (tmp_path / "game").resolve()
    assert response.json() == {"workspace_root": str(expected)}
    assert expected.is_dir()


def test_init_rejects_path_traversal(_client_with_desktop: TestClient) -> None:
    response = _client_with_desktop.post(
        "/v1/desktop/projects/init", json={"project_id": "../escape"}
    )
    assert response.status_code == 400
    assert "project_id must be" in response.json()["detail"]
