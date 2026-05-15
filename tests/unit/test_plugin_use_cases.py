from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.App.plugins.application.use_cases import (
    PluginUseCaseError,
    install_plugin,
    installed,
    list_registries,
    register_registry,
    refresh_registry,
    search,
    uninstall_plugin,
    verify,
)
from backend.App.plugins.domain.registry_listing import (
    PluginListingEntry,
    PluginListingVersion,
    RegistryListing,
)


def _make_listing(plugin_id: str = "test/plugin", version: str = "1.0.0", signed: bool = False) -> RegistryListing:
    sig = "ed25519:abc" if signed else None
    return RegistryListing(
        registry_id="official",
        registry_url="https://example.com/r.json",
        updated_at="2026-05-14",
        plugins=(
            PluginListingEntry(
                id=plugin_id,
                versions=(
                    PluginListingVersion(
                        version=version,
                        url="https://example.com/blobs/plugin.tar.gz",
                        sha256="placeholder",
                        signature=sig,
                    ),
                ),
            ),
        ),
    )


def _build_tarball(tmp: Path, manifest_dict: dict) -> Path:
    tar_path = tmp / "plugin.tar.gz"
    manifest_bytes = json.dumps(manifest_dict).encode()
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="plugin.json")
        info.size = len(manifest_bytes)
        tf.addfile(info, io.BytesIO(manifest_bytes))
    return tar_path


def _manifest_dict(plugin_id: str = "test/plugin", version: str = "1.0.0") -> dict:
    return {
        "id": plugin_id,
        "version": version,
        "kind": "scenario",
        "compat": {"swarm": ">=0.3.0"},
        "title": "Test",
        "description": "Test plugin",
        "author": "tester",
        "license": "MIT",
        "entries": [],
        "depends_on": [],
    }


def _registries_patch(tmp: Path):
    return patch(
        "backend.App.plugins.application.use_cases._REGISTRIES_FILE",
        tmp / "registries.json",
    )


def _store_patch(tmp: Path):
    return patch(
        "backend.App.plugins.infrastructure.plugin_store_fs._PLUGINS_ROOT",
        tmp / "plugins",
    )


def test_register_and_list_registries(tmp_path: Path):
    with _registries_patch(tmp_path):
        register_registry("https://example.com/r.json", "official")
        registries = list_registries()
    assert registries["official"] == "https://example.com/r.json"


def test_register_empty_url_raises(tmp_path: Path):
    with _registries_patch(tmp_path):
        with pytest.raises(PluginUseCaseError, match="URL"):
            register_registry("", "official")


def test_register_empty_name_raises(tmp_path: Path):
    with _registries_patch(tmp_path):
        with pytest.raises(PluginUseCaseError, match="name"):
            register_registry("https://example.com/r.json", "")


def test_refresh_registry_unknown_name(tmp_path: Path):
    with _registries_patch(tmp_path):
        with pytest.raises(PluginUseCaseError, match="not configured"):
            refresh_registry("unknown")


def test_refresh_registry_success(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        result = refresh_registry("official")
    assert result.registry_id == "official"


def test_search_returns_results(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        results = search("test")
    assert len(results) == 1
    assert results[0]["id"] == "test/plugin"


def test_search_empty_query_returns_all(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        results = search("")
    assert len(results) == 1


def test_search_no_match_returns_empty(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        results = search("zzznomatch")
    assert results == []


def test_install_plugin_unsigned_without_flag_raises(tmp_path: Path):
    listing = _make_listing(signed=False)  # noqa: F841
    with _registries_patch(tmp_path), \
         _store_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        with pytest.raises(PluginUseCaseError, match="unsigned"):
            install_plugin("test/plugin", "1.0.0", "official")


def test_install_plugin_unsigned_with_flag_succeeds(tmp_path: Path):
    tar_path = _build_tarball(tmp_path, _manifest_dict())
    sha256 = hashlib.sha256(tar_path.read_bytes()).hexdigest()

    listing_with_sha = RegistryListing(
        registry_id="official",
        registry_url="https://example.com/r.json",
        updated_at="2026-05-14",
        plugins=(
            PluginListingEntry(
                id="test/plugin",
                versions=(
                    PluginListingVersion(
                        version="1.0.0",
                        url="https://example.com/plugin.tar.gz",
                        sha256=sha256,
                        signature=None,
                    ),
                ),
            ),
        ),
    )

    def mock_download(url: str, expected_sha256: str, dest: Path) -> None:
        import shutil
        shutil.copy(tar_path, dest)

    with _registries_patch(tmp_path), \
         _store_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing_with_sha), \
         patch("backend.App.plugins.application.use_cases.download_blob", side_effect=mock_download):
        register_registry("https://example.com/r.json", "official")
        manifest = install_plugin("test/plugin", "1.0.0", "official", allow_unsigned=True)

    assert manifest.id == "test/plugin"


def test_install_plugin_not_found_in_registry(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        with pytest.raises(PluginUseCaseError, match="not found in registry"):
            install_plugin("missing/plugin", "1.0.0", "official", allow_unsigned=True)


def test_install_plugin_version_not_found(tmp_path: Path):
    listing = _make_listing()
    with _registries_patch(tmp_path), \
         patch("backend.App.plugins.application.use_cases.fetch_registry", return_value=listing):
        register_registry("https://example.com/r.json", "official")
        with pytest.raises(PluginUseCaseError, match="Version '99.0.0'"):
            install_plugin("test/plugin", "99.0.0", "official", allow_unsigned=True)


def test_uninstall_unknown_plugin_raises(tmp_path: Path):
    with _store_patch(tmp_path):
        with pytest.raises(PluginUseCaseError, match="not installed"):
            uninstall_plugin("missing/plugin")


def test_installed_returns_manifests(tmp_path: Path):
    with _store_patch(tmp_path):
        result = installed()
    assert result == []


def test_verify_not_installed_raises(tmp_path: Path):
    with _store_patch(tmp_path):
        with pytest.raises(PluginUseCaseError, match="not installed"):
            verify("test/plugin", "1.0.0")
