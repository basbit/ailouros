from __future__ import annotations

import json
import pytest

from backend.App.plugins.domain.registry_listing import (
    RegistryListingError,
    parse_registry_listing,
)


def _make_raw(**overrides: object) -> bytes:
    base: dict = {
        "registry_id": "official",
        "registry_url": "https://plugins.example.com/registry.json",
        "updated_at": "2026-05-14",
        "plugins": [
            {
                "id": "test/plugin",
                "versions": [
                    {
                        "version": "1.0.0",
                        "url": "https://plugins.example.com/blobs/test-1.0.0.tar.gz",
                        "sha256": "abc123def456",
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return json.dumps(base).encode()


def test_parse_happy_path():
    listing = parse_registry_listing(_make_raw())
    assert listing.registry_id == "official"
    assert listing.registry_url == "https://plugins.example.com/registry.json"
    assert listing.updated_at == "2026-05-14"
    assert len(listing.plugins) == 1
    assert listing.plugins[0].id == "test/plugin"
    assert len(listing.plugins[0].versions) == 1
    v = listing.plugins[0].versions[0]
    assert v.version == "1.0.0"
    assert v.sha256 == "abc123def456"
    assert v.signature is None


def test_parse_with_signature():
    raw = json.loads(_make_raw())
    raw["plugins"][0]["versions"][0]["signature"] = "ed25519:sig123"
    listing = parse_registry_listing(json.dumps(raw).encode())
    assert listing.plugins[0].versions[0].signature == "ed25519:sig123"


def test_parse_multiple_plugins_and_versions():
    raw = {
        "registry_id": "r",
        "registry_url": "https://r.example.com/r.json",
        "updated_at": "2026-01-01",
        "plugins": [
            {
                "id": "a/b",
                "versions": [
                    {"version": "1.0.0", "url": "http://x.example.com/a.tar.gz", "sha256": "aa"},
                    {"version": "2.0.0", "url": "http://x.example.com/b.tar.gz", "sha256": "bb"},
                ],
            },
            {
                "id": "c/d",
                "versions": [
                    {"version": "0.1.0", "url": "http://x.example.com/c.tar.gz", "sha256": "cc"},
                ],
            },
        ],
    }
    listing = parse_registry_listing(json.dumps(raw).encode())
    assert len(listing.plugins) == 2
    assert len(listing.plugins[0].versions) == 2


def test_parse_invalid_json():
    with pytest.raises(RegistryListingError, match="not valid"):
        parse_registry_listing(b"not json{{")


def test_parse_root_not_object():
    with pytest.raises(RegistryListingError, match="root must be an object"):
        parse_registry_listing(b"[1, 2, 3]")


def test_parse_missing_registry_id():
    raw = json.loads(_make_raw())
    del raw["registry_id"]
    with pytest.raises(RegistryListingError, match="registry_id"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_missing_registry_url():
    raw = json.loads(_make_raw())
    del raw["registry_url"]
    with pytest.raises(RegistryListingError, match="registry_url"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_missing_updated_at():
    raw = json.loads(_make_raw())
    del raw["updated_at"]
    with pytest.raises(RegistryListingError, match="updated_at"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_plugins_not_list():
    with pytest.raises(RegistryListingError, match="plugins"):
        parse_registry_listing(_make_raw(plugins="not_a_list"))


def test_parse_plugin_missing_id():
    raw = json.loads(_make_raw())
    raw["plugins"][0].pop("id")
    with pytest.raises(RegistryListingError, match="id"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_version_missing_sha256():
    raw = json.loads(_make_raw())
    raw["plugins"][0]["versions"][0].pop("sha256")
    with pytest.raises(RegistryListingError, match="sha256"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_version_missing_url():
    raw = json.loads(_make_raw())
    raw["plugins"][0]["versions"][0].pop("url")
    with pytest.raises(RegistryListingError, match="url"):
        parse_registry_listing(json.dumps(raw).encode())


def test_parse_version_not_object():
    raw = json.loads(_make_raw())
    raw["plugins"][0]["versions"] = ["not_an_object"]
    with pytest.raises(RegistryListingError, match="object"):
        parse_registry_listing(json.dumps(raw).encode())


def test_empty_plugins_list():
    listing = parse_registry_listing(_make_raw(plugins=[]))
    assert listing.plugins == ()


def test_dataclass_is_frozen():
    listing = parse_registry_listing(_make_raw())
    with pytest.raises((AttributeError, TypeError)):
        listing.registry_id = "changed"  # type: ignore[misc]
