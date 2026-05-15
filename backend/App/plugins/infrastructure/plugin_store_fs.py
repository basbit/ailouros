from __future__ import annotations

import hashlib
import shutil
import tarfile
from pathlib import Path
from typing import Optional

from backend.App.plugins.domain.plugin_manifest import PluginManifest, PluginManifestError, parse_manifest
from backend.App.paths import APP_ROOT

_PLUGINS_ROOT = APP_ROOT / "config" / "plugins"


class PluginIntegrityError(ValueError):
    pass


class PluginNotFoundError(KeyError):
    pass


def _plugins_root() -> Path:
    return _PLUGINS_ROOT


def _plugin_dir(plugin_id: str, version: str) -> Path:
    safe_id = plugin_id.replace("/", "__")
    return _plugins_root() / safe_id / version


def _guard_traversal(base: Path, candidate: Path) -> None:
    try:
        candidate.resolve().relative_to(base.resolve())
    except ValueError:
        raise PluginIntegrityError(
            f"Path traversal detected: '{candidate}' escapes plugin directory '{base}'"
        )


def installed_plugins() -> list[PluginManifest]:
    root = _plugins_root()
    if not root.exists():
        return []
    manifests: list[PluginManifest] = []
    for manifest_path in sorted(root.glob("**/plugin.json")):
        try:
            raw = manifest_path.read_bytes()
            manifest = parse_manifest(raw)
            manifests.append(manifest)
        except (OSError, PluginManifestError) as exc:
            raise PluginIntegrityError(
                f"Corrupt plugin manifest at {manifest_path}: {exc}"
            ) from exc
    return manifests


def is_installed(plugin_id: str, version: str) -> bool:
    return (_plugin_dir(plugin_id, version) / "plugin.json").is_file()


def install_from_path(tar_path: Path, expected_sha256: Optional[str] = None) -> PluginManifest:
    if not tar_path.is_file():
        raise PluginIntegrityError(f"Tarball not found: {tar_path}")

    if expected_sha256 is not None:
        actual = hashlib.sha256(tar_path.read_bytes()).hexdigest()
        if actual != expected_sha256.lower():
            raise PluginIntegrityError(
                f"SHA-256 mismatch for {tar_path.name}: "
                f"expected {expected_sha256}, got {actual}"
            )

    if not tarfile.is_tarfile(tar_path):
        raise PluginIntegrityError(
            f"'{tar_path}' is not a valid tar archive. "
            "Plugin must be a .tar.gz file."
        )

    with tarfile.open(tar_path, "r:*") as tf:
        members = tf.getmembers()
        flat_members = [m for m in members if m.name == "plugin.json" or m.name.startswith("./plugin.json")]
        if not flat_members:
            nested = [m for m in members if m.name.lstrip("./").split("/")[-1] == "plugin.json" and m.name.lstrip("./").count("/") <= 1]
            if not nested:
                raise PluginIntegrityError(
                    f"plugin.json not found at tarball root in {tar_path.name}. "
                    "The archive must contain plugin.json at the top level."
                )
            manifest_member = nested[0]
        else:
            manifest_member = flat_members[0]

        manifest_file = tf.extractfile(manifest_member)
        if manifest_file is None:
            raise PluginIntegrityError(f"Cannot read plugin.json from {tar_path.name}")
        manifest_bytes = manifest_file.read()

    try:
        manifest = parse_manifest(manifest_bytes)
    except PluginManifestError as exc:
        raise PluginIntegrityError(f"Invalid plugin.json in {tar_path.name}: {exc}") from exc

    dest = _plugin_dir(manifest.id, manifest.version)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(tar_path, "r:*") as tf:
        for member in tf.getmembers():
            norm = member.name.lstrip("./")
            if not norm:
                continue
            candidate = (dest / norm).resolve()
            _guard_traversal(dest, candidate)
            tf.extract(member, path=dest, set_attrs=False, filter="data")

    return manifest


def uninstall(plugin_id: str, version: str) -> None:
    plugin_dir = _plugin_dir(plugin_id, version)
    if not plugin_dir.exists():
        raise PluginNotFoundError(
            f"Plugin '{plugin_id}@{version}' is not installed. "
            f"Expected directory: {plugin_dir}"
        )
    shutil.rmtree(plugin_dir)
    parent = plugin_dir.parent
    try:
        if parent != _plugins_root() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass
