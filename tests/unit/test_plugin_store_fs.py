from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.App.plugins.infrastructure.plugin_store_fs import (
    PluginIntegrityError,
    PluginNotFoundError,
    install_from_path,
    installed_plugins,
    is_installed,
    uninstall,
)


def _make_manifest(**overrides: object) -> dict:
    base = {
        "id": "test/plugin",
        "version": "1.0.0",
        "kind": "scenario",
        "compat": {"swarm": ">=0.3.0"},
        "title": "Test Plugin",
        "description": "A test plugin",
        "author": "tester",
        "license": "MIT",
        "entries": [{"path": "scenarios/test.json", "target": "config/scenarios/"}],
        "depends_on": [],
    }
    base.update(overrides)
    return base


def _build_tarball(tmp: Path, manifest_dict: dict, extra_files: dict[str, bytes] | None = None) -> Path:
    tar_path = tmp / "plugin.tar.gz"
    manifest_bytes = json.dumps(manifest_dict).encode()
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="plugin.json")
        info.size = len(manifest_bytes)
        tf.addfile(info, io.BytesIO(manifest_bytes))
        if extra_files:
            for name, content in extra_files.items():
                ti = tarfile.TarInfo(name=name)
                ti.size = len(content)
                tf.addfile(ti, io.BytesIO(content))
    return tar_path


def _plugins_root_patch(tmp: Path):
    return patch(
        "backend.App.plugins.infrastructure.plugin_store_fs._PLUGINS_ROOT",
        tmp / "plugins",
    )


def test_install_and_list(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        manifest = install_from_path(tar)
        assert manifest.id == "test/plugin"
        assert manifest.version == "1.0.0"

        installed = installed_plugins()
        assert len(installed) == 1
        assert installed[0].id == "test/plugin"


def test_is_installed(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        assert not is_installed("test/plugin", "1.0.0")
        install_from_path(tar)
        assert is_installed("test/plugin", "1.0.0")


def test_uninstall(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        install_from_path(tar)
        assert is_installed("test/plugin", "1.0.0")
        uninstall("test/plugin", "1.0.0")
        assert not is_installed("test/plugin", "1.0.0")
        assert installed_plugins() == []


def test_uninstall_not_found_raises(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        with pytest.raises(PluginNotFoundError, match="not installed"):
            uninstall("missing/plugin", "1.0.0")


def test_install_sha256_mismatch(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        with pytest.raises(PluginIntegrityError, match="SHA-256 mismatch"):
            install_from_path(tar, expected_sha256="deadbeef")


def test_install_correct_sha256(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        sha256 = hashlib.sha256(tar.read_bytes()).hexdigest()
        manifest = install_from_path(tar, expected_sha256=sha256)
        assert manifest.id == "test/plugin"


def test_install_missing_tarball(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        with pytest.raises(PluginIntegrityError, match="not found"):
            install_from_path(tmp_path / "nonexistent.tar.gz")


def test_install_not_a_tarball(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        bad = tmp_path / "bad.tar.gz"
        bad.write_bytes(b"this is not a tarball")
        with pytest.raises(PluginIntegrityError, match="valid tar"):
            install_from_path(bad)


def test_install_missing_plugin_json(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar_path = tmp_path / "empty.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            content = b"hello"
            ti = tarfile.TarInfo(name="some_other_file.txt")
            ti.size = len(content)
            tf.addfile(ti, io.BytesIO(content))
        with pytest.raises(PluginIntegrityError, match="plugin.json"):
            install_from_path(tar_path)


def test_installed_empty(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        assert installed_plugins() == []


def test_install_idempotent(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(tmp_path, _make_manifest())
        install_from_path(tar)
        tar2 = _build_tarball(tmp_path, _make_manifest(title="Updated"))
        manifest = install_from_path(tar2)
        assert manifest.title == "Updated"
        assert len(installed_plugins()) == 1


def test_install_traversal_guard(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar_path = tmp_path / "evil.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            manifest_bytes = json.dumps(_make_manifest()).encode()
            mi = tarfile.TarInfo(name="plugin.json")
            mi.size = len(manifest_bytes)
            tf.addfile(mi, io.BytesIO(manifest_bytes))
            evil_content = b"evil"
            ei = tarfile.TarInfo(name="../../etc/evil.txt")
            ei.size = len(evil_content)
            tf.addfile(ei, io.BytesIO(evil_content))
        with pytest.raises((PluginIntegrityError, Exception)):
            install_from_path(tar_path)


def test_install_with_extra_files(tmp_path: Path):
    with _plugins_root_patch(tmp_path):
        tar = _build_tarball(
            tmp_path,
            _make_manifest(),
            extra_files={"scenarios/test.json": b'{"id": "test"}'},
        )
        install_from_path(tar)
        plugin_dir = tmp_path / "plugins" / "test__plugin" / "1.0.0"
        assert (plugin_dir / "scenarios" / "test.json").exists()
