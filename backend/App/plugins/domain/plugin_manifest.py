from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Optional

PluginKind = Literal["scenario", "agent_role", "prompt", "skill", "mcp_server", "power"]

_REQUIRED_FIELDS = ("id", "version", "kind", "title", "description", "author", "license", "entries")
_VALID_KINDS = {"scenario", "agent_role", "prompt", "skill", "mcp_server", "power"}


class PluginManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginEntry:
    path: str
    target: str


@dataclass(frozen=True)
class PluginManifest:
    id: str
    version: str
    kind: PluginKind
    compat: str
    title: str
    description: str
    author: str
    license: str
    signature: Optional[str]
    entries: tuple[PluginEntry, ...]
    depends_on: tuple[str, ...]


def _parse_entries(raw: object, context: str) -> tuple[PluginEntry, ...]:
    if not isinstance(raw, list):
        raise PluginManifestError(f"{context}: 'entries' must be a list, got {type(raw).__name__}")
    result: list[PluginEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PluginManifestError(f"{context}: entries[{i}] must be an object")
        path = item.get("path")
        target = item.get("target")
        if not isinstance(path, str) or not path:
            raise PluginManifestError(f"{context}: entries[{i}].path is missing or not a string")
        if not isinstance(target, str) or not target:
            raise PluginManifestError(f"{context}: entries[{i}].target is missing or not a string")
        if ".." in path.split("/") or path.startswith("/"):
            raise PluginManifestError(
                f"{context}: entries[{i}].path '{path}' must be relative and must not contain '..'"
            )
        result.append(PluginEntry(path=path, target=target))
    return tuple(result)


def parse_manifest(json_bytes: bytes) -> PluginManifest:
    try:
        data = json.loads(json_bytes)
    except json.JSONDecodeError as exc:
        raise PluginManifestError(f"plugin.json is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise PluginManifestError("plugin.json root must be a JSON object")

    for field in _REQUIRED_FIELDS:
        if field not in data:
            raise PluginManifestError(f"plugin.json missing required field '{field}'")

    kind = data["kind"]
    if kind not in _VALID_KINDS:
        raise PluginManifestError(
            f"plugin.json 'kind' must be one of {sorted(_VALID_KINDS)}, got '{kind}'"
        )

    compat_raw = data.get("compat")
    if isinstance(compat_raw, dict):
        compat = compat_raw.get("swarm", "")
        if not isinstance(compat, str):
            raise PluginManifestError("plugin.json 'compat.swarm' must be a string")
    elif isinstance(compat_raw, str):
        compat = compat_raw
    elif compat_raw is None:
        compat = ""
    else:
        raise PluginManifestError("plugin.json 'compat' must be an object or string")

    for str_field in ("id", "version", "title", "description", "author", "license"):
        val = data[str_field]
        if not isinstance(val, str) or not val.strip():
            raise PluginManifestError(
                f"plugin.json field '{str_field}' must be a non-empty string, got {val!r}"
            )

    signature = data.get("signature")
    if signature is not None and not isinstance(signature, str):
        raise PluginManifestError("plugin.json 'signature' must be a string or absent")

    depends_raw = data.get("depends_on", [])
    if not isinstance(depends_raw, list):
        raise PluginManifestError("plugin.json 'depends_on' must be a list")
    for i, dep in enumerate(depends_raw):
        if not isinstance(dep, str) or not dep.strip():
            raise PluginManifestError(f"plugin.json depends_on[{i}] must be a non-empty string")
    depends_on = tuple(str(d) for d in depends_raw)

    entries = _parse_entries(data["entries"], "plugin.json")

    return PluginManifest(
        id=data["id"],
        version=data["version"],
        kind=kind,
        compat=compat,
        title=data["title"],
        description=data["description"],
        author=data["author"],
        license=data["license"],
        signature=signature,
        entries=entries,
        depends_on=depends_on,
    )


def serialise_manifest(manifest: PluginManifest) -> str:
    return json.dumps(
        {
            "id": manifest.id,
            "version": manifest.version,
            "kind": manifest.kind,
            "compat": {"swarm": manifest.compat},
            "title": manifest.title,
            "description": manifest.description,
            "author": manifest.author,
            "license": manifest.license,
            "signature": manifest.signature,
            "entries": [{"path": e.path, "target": e.target} for e in manifest.entries],
            "depends_on": list(manifest.depends_on),
        },
        indent=2,
    )
