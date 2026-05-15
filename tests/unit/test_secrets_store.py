from __future__ import annotations

import json

import pytest

from backend.App.shared.infrastructure import secrets_store


@pytest.fixture()
def secrets_path(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    monkeypatch.setenv("SWARM_SECRETS_PATH", str(path))
    return path


def test_load_unknown_secret_returns_none(secrets_path):
    assert secrets_store.load_secret("web_search.tavily") is None


def test_save_and_load_roundtrip(secrets_path):
    secrets_store.save_secret("web_search.tavily", "tvly-abc")
    assert secrets_store.load_secret("web_search.tavily") == "tvly-abc"


def test_save_creates_file_with_strict_permissions(secrets_path):
    secrets_store.save_secret("a", "value-1")
    assert secrets_path.is_file()
    data = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert data == {"a": "value-1"}


def test_save_blank_value_rejected(secrets_path):
    with pytest.raises(ValueError):
        secrets_store.save_secret("a", "   ")


def test_save_blank_name_rejected(secrets_path):
    with pytest.raises(ValueError):
        secrets_store.save_secret("  ", "v")


def test_delete_existing_secret(secrets_path):
    secrets_store.save_secret("k", "v")
    assert secrets_store.delete_secret("k") is True
    assert secrets_store.load_secret("k") is None


def test_delete_missing_secret_returns_false(secrets_path):
    assert secrets_store.delete_secret("nope") is False


def test_list_secret_names_sorted(secrets_path):
    secrets_store.save_secret("b", "1")
    secrets_store.save_secret("a", "2")
    assert secrets_store.list_secret_names() == ["a", "b"]
