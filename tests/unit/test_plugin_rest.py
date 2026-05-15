from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from backend.UI.REST.controllers.plugins import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _manifest_dict(**overrides: Any) -> dict:
    base = {
        "id": "test/plugin",
        "version": "1.0.0",
        "kind": "scenario",
        "compat": ">=0.3.0",
        "title": "Test",
        "description": "Test plugin",
        "author": "tester",
        "license": "MIT",
        "signed": False,
        "depends_on": [],
        "entries": [],
    }
    base.update(overrides)
    return base


def test_list_installed_empty(client: TestClient):
    with patch("backend.UI.REST.controllers.plugins.installed", return_value=[]):
        resp = client.get("/v1/plugins")
    assert resp.status_code == 200
    assert resp.json() == {"plugins": []}


def test_list_installed_returns_manifests(client: TestClient):
    m = MagicMock()
    m.id = "test/plugin"
    m.version = "1.0.0"
    m.kind = "scenario"
    m.compat = ">=0.3.0"
    m.title = "Test"
    m.description = "Test plugin"
    m.author = "tester"
    m.license = "MIT"
    m.signature = None
    m.depends_on = []
    m.entries = []
    with patch("backend.UI.REST.controllers.plugins.installed", return_value=[m]):
        resp = client.get("/v1/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["plugins"]) == 1
    assert data["plugins"][0]["id"] == "test/plugin"


def test_get_registries_empty(client: TestClient):
    with patch("backend.UI.REST.controllers.plugins.list_registries", return_value={}):
        resp = client.get("/v1/plugins/registries")
    assert resp.status_code == 200
    assert resp.json() == {"registries": {}}


def test_add_registry_success(client: TestClient):
    with patch("backend.UI.REST.controllers.plugins.register_registry"):
        resp = client.post(
            "/v1/plugins/registries",
            json={"url": "https://example.com/r.json", "name": "official"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["registered"] is True
    assert data["name"] == "official"


def test_add_registry_validation_error(client: TestClient):
    from backend.App.plugins.application.use_cases import PluginUseCaseError
    with patch(
        "backend.UI.REST.controllers.plugins.register_registry",
        side_effect=PluginUseCaseError("URL must not be empty"),
    ):
        resp = client.post(
            "/v1/plugins/registries",
            json={"url": "", "name": "official"},
        )
    assert resp.status_code == 422


def test_refresh_registry_success(client: TestClient):
    mock_listing = MagicMock()
    mock_listing.registry_id = "official"
    mock_listing.registry_url = "https://example.com/r.json"
    mock_listing.updated_at = "2026-05-14"
    mock_listing.plugins = []
    with patch("backend.UI.REST.controllers.plugins.refresh_registry", return_value=mock_listing):
        resp = client.post("/v1/plugins/registries/official/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert data["registry_id"] == "official"
    assert data["plugin_count"] == 0


def test_refresh_registry_unknown_raises_422(client: TestClient):
    from backend.App.plugins.application.use_cases import PluginUseCaseError
    with patch(
        "backend.UI.REST.controllers.plugins.refresh_registry",
        side_effect=PluginUseCaseError("not configured"),
    ):
        resp = client.post("/v1/plugins/registries/unknown/refresh")
    assert resp.status_code == 422


def test_search_returns_results(client: TestClient):
    with patch(
        "backend.UI.REST.controllers.plugins.search",
        return_value=[{"id": "test/plugin", "registry": "official", "versions": []}],
    ):
        resp = client.get("/v1/plugins/search?q=test")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1


def test_search_empty_query(client: TestClient):
    with patch("backend.UI.REST.controllers.plugins.search", return_value=[]):
        resp = client.get("/v1/plugins/search")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_install_plugin_success(client: TestClient):
    m = MagicMock()
    m.id = "test/plugin"
    m.version = "1.0.0"
    m.kind = "scenario"
    m.compat = ">=0.3.0"
    m.title = "Test"
    m.description = "A plugin"
    m.author = "tester"
    m.license = "MIT"
    m.signature = None
    m.depends_on = []
    m.entries = []
    with patch("backend.UI.REST.controllers.plugins.install_plugin", return_value=m):
        resp = client.post(
            "/v1/plugins/install",
            json={"id": "test/plugin", "version": "1.0.0", "registry": "official"},
        )
    assert resp.status_code == 200
    assert resp.json()["installed"] is True


def test_install_plugin_unsigned_error(client: TestClient):
    from backend.App.plugins.application.use_cases import PluginUseCaseError
    with patch(
        "backend.UI.REST.controllers.plugins.install_plugin",
        side_effect=PluginUseCaseError("is unsigned"),
    ):
        resp = client.post(
            "/v1/plugins/install",
            json={"id": "test/plugin", "version": "1.0.0", "registry": "official"},
        )
    assert resp.status_code == 422
    assert "unsigned" in resp.json()["detail"]


def test_uninstall_plugin_success(client: TestClient):
    with patch("backend.UI.REST.controllers.plugins.uninstall_plugin"):
        resp = client.delete("/v1/plugins/test/plugin")
    assert resp.status_code == 200
    assert resp.json()["uninstalled"] is True


def test_uninstall_plugin_not_found(client: TestClient):
    from backend.App.plugins.infrastructure.plugin_store_fs import PluginNotFoundError
    with patch(
        "backend.UI.REST.controllers.plugins.uninstall_plugin",
        side_effect=PluginNotFoundError("not installed"),
    ):
        resp = client.delete("/v1/plugins/missing/plugin")
    assert resp.status_code == 404
