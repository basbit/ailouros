from __future__ import annotations

import json
import pytest

from backend.App.plugins.domain.plugin_manifest import (
    PluginManifestError,
    parse_manifest,
    serialise_manifest,
)


def _make_raw(**overrides: object) -> bytes:
    base: dict = {
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
    return json.dumps(base).encode()


def test_parse_happy_path():
    m = parse_manifest(_make_raw())
    assert m.id == "test/plugin"
    assert m.version == "1.0.0"
    assert m.kind == "scenario"
    assert m.compat == ">=0.3.0"
    assert m.title == "Test Plugin"
    assert len(m.entries) == 1
    assert m.entries[0].path == "scenarios/test.json"
    assert m.entries[0].target == "config/scenarios/"
    assert m.depends_on == ()
    assert m.signature is None


def test_parse_all_kinds():
    for kind in ("scenario", "agent_role", "prompt", "skill", "mcp_server", "power"):
        m = parse_manifest(_make_raw(kind=kind))
        assert m.kind == kind


def test_parse_with_signature():
    m = parse_manifest(_make_raw(signature="ed25519:abc123"))
    assert m.signature == "ed25519:abc123"


def test_parse_with_depends_on():
    m = parse_manifest(_make_raw(depends_on=["other/plugin@>=1.0"]))
    assert m.depends_on == ("other/plugin@>=1.0",)


def test_parse_compat_as_string():
    m = parse_manifest(_make_raw(compat=">=0.3.0,<0.5.0"))
    assert m.compat == ">=0.3.0,<0.5.0"


def test_parse_compat_absent():
    raw = json.loads(_make_raw())
    del raw["compat"]
    m = parse_manifest(json.dumps(raw).encode())
    assert m.compat == ""


def test_parse_missing_required_field():
    for field in ("id", "version", "kind", "title", "description", "author", "license", "entries"):
        raw = json.loads(_make_raw())
        del raw[field]
        with pytest.raises(PluginManifestError, match=field):
            parse_manifest(json.dumps(raw).encode())


def test_parse_invalid_json():
    with pytest.raises(PluginManifestError, match="not valid JSON"):
        parse_manifest(b"not json {{{")


def test_parse_root_not_object():
    with pytest.raises(PluginManifestError, match="root must be a JSON object"):
        parse_manifest(b"[1, 2, 3]")


def test_parse_invalid_kind():
    with pytest.raises(PluginManifestError, match="kind"):
        parse_manifest(_make_raw(kind="invalid_kind"))


def test_parse_empty_string_field():
    with pytest.raises(PluginManifestError, match="id"):
        parse_manifest(_make_raw(id=""))


def test_parse_entries_not_list():
    with pytest.raises(PluginManifestError, match="entries"):
        parse_manifest(_make_raw(entries="not_a_list"))


def test_parse_entry_missing_path():
    with pytest.raises(PluginManifestError, match="path"):
        parse_manifest(_make_raw(entries=[{"target": "config/"}]))


def test_parse_entry_missing_target():
    with pytest.raises(PluginManifestError, match="target"):
        parse_manifest(_make_raw(entries=[{"path": "scenarios/x.json"}]))


def test_parse_entry_path_traversal():
    with pytest.raises(PluginManifestError, match="traversal|\\.\\."):
        parse_manifest(_make_raw(entries=[{"path": "../../etc/passwd", "target": "config/"}]))


def test_parse_entry_absolute_path():
    with pytest.raises(PluginManifestError, match="relative"):
        parse_manifest(_make_raw(entries=[{"path": "/etc/passwd", "target": "config/"}]))


def test_parse_depends_on_not_list():
    with pytest.raises(PluginManifestError, match="depends_on"):
        parse_manifest(_make_raw(depends_on="not_a_list"))


def test_parse_signature_wrong_type():
    with pytest.raises(PluginManifestError, match="signature"):
        parse_manifest(_make_raw(signature=12345))


def test_serialise_roundtrip():
    m = parse_manifest(_make_raw())
    serialised = serialise_manifest(m)
    m2 = parse_manifest(serialised.encode())
    assert m == m2


def test_serialise_contains_all_fields():
    m = parse_manifest(_make_raw(signature="ed25519:abc"))
    s = serialise_manifest(m)
    d = json.loads(s)
    assert d["id"] == "test/plugin"
    assert d["signature"] == "ed25519:abc"
    assert d["entries"][0]["path"] == "scenarios/test.json"


def test_manifest_is_frozen():
    m = parse_manifest(_make_raw())
    with pytest.raises((AttributeError, TypeError)):
        m.id = "changed"  # type: ignore[misc]
