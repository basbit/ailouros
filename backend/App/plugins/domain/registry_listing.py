from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


class RegistryListingError(ValueError):
    pass


@dataclass(frozen=True)
class PluginListingVersion:
    version: str
    url: str
    sha256: str
    signature: Optional[str]


@dataclass(frozen=True)
class PluginListingEntry:
    id: str
    versions: tuple[PluginListingVersion, ...]


@dataclass(frozen=True)
class RegistryListing:
    registry_id: str
    registry_url: str
    updated_at: str
    plugins: tuple[PluginListingEntry, ...]


def _parse_version_entry(raw: object, context: str) -> PluginListingVersion:
    if not isinstance(raw, dict):
        raise RegistryListingError(f"{context}: version entry must be an object")
    for field in ("version", "url", "sha256"):
        if field not in raw:
            raise RegistryListingError(f"{context}: version entry missing required field '{field}'")
        if not isinstance(raw[field], str) or not raw[field]:
            raise RegistryListingError(
                f"{context}: version entry field '{field}' must be a non-empty string"
            )
    sig = raw.get("signature")
    if sig is not None and not isinstance(sig, str):
        raise RegistryListingError(f"{context}: 'signature' must be a string or absent")
    return PluginListingVersion(
        version=raw["version"],
        url=raw["url"],
        sha256=raw["sha256"],
        signature=sig,
    )


def _parse_plugin_entry(raw: object, idx: int) -> PluginListingEntry:
    context = f"plugins[{idx}]"
    if not isinstance(raw, dict):
        raise RegistryListingError(f"{context}: must be an object")
    plugin_id = raw.get("id")
    if not isinstance(plugin_id, str) or not plugin_id:
        raise RegistryListingError(f"{context}: 'id' must be a non-empty string")
    versions_raw = raw.get("versions")
    if not isinstance(versions_raw, list):
        raise RegistryListingError(f"{context}: 'versions' must be a list")
    versions = tuple(
        _parse_version_entry(v, f"{context}.versions[{i}]")
        for i, v in enumerate(versions_raw)
    )
    return PluginListingEntry(id=plugin_id, versions=versions)


def parse_registry_listing(json_bytes: bytes) -> RegistryListing:
    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError as exc:
        raise RegistryListingError(f"registry JSON is not valid: {exc}") from exc

    if not isinstance(data, dict):
        raise RegistryListingError("registry JSON root must be an object")

    for field in ("registry_id", "registry_url", "updated_at"):
        val = data.get(field)
        if not isinstance(val, str) or not val:
            raise RegistryListingError(
                f"registry JSON missing or invalid required field '{field}'"
            )

    plugins_raw = data.get("plugins")
    if not isinstance(plugins_raw, list):
        raise RegistryListingError("registry JSON 'plugins' must be a list")

    plugins = tuple(_parse_plugin_entry(p, i) for i, p in enumerate(plugins_raw))

    return RegistryListing(
        registry_id=data["registry_id"],
        registry_url=data["registry_url"],
        updated_at=data["updated_at"],
        plugins=plugins,
    )
