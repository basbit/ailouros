from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Optional

from backend.App.plugins.domain.plugin_manifest import PluginManifest, PluginManifestError, parse_manifest
from backend.App.plugins.domain.registry_listing import PluginListingVersion, RegistryListing
from backend.App.plugins.domain.semver import SemverRange, SemverError
from backend.App.plugins.infrastructure.plugin_store_fs import (
    install_from_path,
    installed_plugins,
    is_installed,
    uninstall,
)
from backend.App.plugins.infrastructure.registry_client import (
    download_blob,
    fetch_registry,
)
from backend.App.paths import APP_ROOT

logger = logging.getLogger(__name__)

_REGISTRIES_FILE = APP_ROOT / "config" / "plugins" / "registries.json"

_SWARM_VERSION = "0.3.0"


class PluginUseCaseError(ValueError):
    pass


def _load_registries() -> dict[str, str]:
    if not _REGISTRIES_FILE.exists():
        return {}
    try:
        raw = json.loads(_REGISTRIES_FILE.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PluginUseCaseError(
            f"registries.json is corrupt or unreadable at {_REGISTRIES_FILE}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise PluginUseCaseError(
            f"registries.json must be a JSON object, got {type(raw).__name__}"
        )
    return {str(k): str(v) for k, v in raw.items()}


def _save_registries(registries: dict[str, str]) -> None:
    _REGISTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRIES_FILE.write_text(json.dumps(registries, indent=2), encoding="utf-8")


def register_registry(url: str, name: str) -> None:
    if not url.strip():
        raise PluginUseCaseError("Registry URL must not be empty")
    if not name.strip():
        raise PluginUseCaseError("Registry name must not be empty")
    registries = _load_registries()
    registries[name] = url
    _save_registries(registries)


def list_registries() -> dict[str, str]:
    return _load_registries()


def refresh_registry(name: str) -> RegistryListing:
    registries = _load_registries()
    if name not in registries:
        raise PluginUseCaseError(
            f"Registry '{name}' is not configured. "
            f"Known registries: {sorted(registries.keys()) or '(none)'}"
        )
    url = registries[name]
    return fetch_registry(url)


def search(query: str, registry_name: Optional[str] = None) -> list[dict[str, Any]]:
    registries = _load_registries()
    if not registries:
        return []

    names_to_search = [registry_name] if registry_name else list(registries.keys())
    results: list[dict[str, Any]] = []
    q = query.strip().lower()

    for name in names_to_search:
        if name not in registries:
            raise PluginUseCaseError(
                f"Registry '{name}' is not configured. "
                f"Known registries: {sorted(registries.keys())}"
            )
        listing = fetch_registry(registries[name])
        for plugin in listing.plugins:
            if q and q not in plugin.id.lower():
                continue
            results.append({
                "id": plugin.id,
                "registry": name,
                "versions": [
                    {
                        "version": v.version,
                        "url": v.url,
                        "sha256": v.sha256,
                        "signed": v.signature is not None,
                    }
                    for v in plugin.versions
                ],
            })

    return results


def install_plugin(
    plugin_id: str,
    version: str,
    registry_name: str,
    *,
    allow_unsigned: bool = False,
) -> PluginManifest:
    registries = _load_registries()
    if registry_name not in registries:
        raise PluginUseCaseError(
            f"Registry '{registry_name}' is not configured. "
            f"Known registries: {sorted(registries.keys()) or '(none)'}"
        )

    listing = fetch_registry(registries[registry_name])

    plugin_entry = next((p for p in listing.plugins if p.id == plugin_id), None)
    if plugin_entry is None:
        raise PluginUseCaseError(
            f"Plugin '{plugin_id}' not found in registry '{registry_name}'"
        )

    version_entry: Optional[PluginListingVersion] = next(
        (v for v in plugin_entry.versions if v.version == version), None
    )
    if version_entry is None:
        available = [v.version for v in plugin_entry.versions]
        raise PluginUseCaseError(
            f"Version '{version}' of plugin '{plugin_id}' not found in registry '{registry_name}'. "
            f"Available versions: {available}"
        )

    if version_entry.signature is None and not allow_unsigned:
        raise PluginUseCaseError(
            f"Plugin '{plugin_id}@{version}' from '{registry_name}' is unsigned. "
            "Pass allow_unsigned=True to install unsigned plugins. "
            "WARNING: unsigned plugins have not been verified by the publisher."
        )

    if version_entry.signature is not None:
        logger.warning(
            "plugin_install: signature verification not implemented (v1 stub). "
            "TODO: implement ed25519 signature check against plugin_trust.json. "
            "Plugin '%s@%s' — treating as trusted for now.",
            plugin_id,
            version,
        )

    with tempfile.TemporaryDirectory(prefix="swarm_plugin_dl_") as tmp:
        tar_path = Path(tmp) / f"{plugin_id.replace('/', '__')}-{version}.tar.gz"
        download_blob(version_entry.url, version_entry.sha256, tar_path)
        manifest = install_from_path(tar_path, expected_sha256=version_entry.sha256)

    if manifest.compat:
        try:
            semver_range = SemverRange.parse(manifest.compat)
            if not semver_range.matches(_SWARM_VERSION):
                raise PluginUseCaseError(
                    f"Plugin '{plugin_id}@{version}' requires swarm version '{manifest.compat}', "
                    f"but installed version is '{_SWARM_VERSION}'. "
                    "Update swarm or find a compatible plugin version."
                )
        except SemverError as exc:
            raise PluginUseCaseError(
                f"Plugin '{plugin_id}@{version}' has invalid compat range '{manifest.compat}': {exc}"
            ) from exc

    return manifest


def uninstall_plugin(plugin_id: str, version: Optional[str] = None) -> None:
    if version is not None:
        uninstall(plugin_id, version)
        return

    all_installed = installed_plugins()
    matching = [m for m in all_installed if m.id == plugin_id]
    if not matching:
        raise PluginUseCaseError(
            f"Plugin '{plugin_id}' is not installed"
        )
    for manifest in matching:
        uninstall(manifest.id, manifest.version)


def installed() -> list[PluginManifest]:
    return installed_plugins()


def verify(plugin_id: str, version: str) -> dict[str, Any]:
    if not is_installed(plugin_id, version):
        raise PluginUseCaseError(
            f"Plugin '{plugin_id}@{version}' is not installed"
        )

    from backend.App.plugins.infrastructure.plugin_store_fs import _plugin_dir
    manifest_path = _plugin_dir(plugin_id, version) / "plugin.json"
    try:
        manifest = parse_manifest(manifest_path.read_bytes())
    except (OSError, PluginManifestError) as exc:
        raise PluginUseCaseError(
            f"Plugin manifest for '{plugin_id}@{version}' is corrupt: {exc}"
        ) from exc

    compat_ok = True
    compat_message = "no compat constraint"
    if manifest.compat:
        try:
            semver_range = SemverRange.parse(manifest.compat)
            compat_ok = semver_range.matches(_SWARM_VERSION)
            compat_message = f"range '{manifest.compat}' vs swarm '{_SWARM_VERSION}'"
        except SemverError as exc:
            compat_ok = False
            compat_message = f"invalid range: {exc}"

    signed = manifest.signature is not None
    signature_verified = False
    if signed:
        signature_verified = False
        logger.warning(
            "plugin_verify: signature verification is a v1 stub. "
            "TODO: implement ed25519 verification. Plugin: '%s@%s'",
            plugin_id,
            version,
        )

    return {
        "id": manifest.id,
        "version": manifest.version,
        "kind": manifest.kind,
        "compat_ok": compat_ok,
        "compat_detail": compat_message,
        "signed": signed,
        "signature_verified": signature_verified,
        "depends_on": list(manifest.depends_on),
    }
