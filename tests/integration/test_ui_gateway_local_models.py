"""Tests for ui_gateway._local_models_payload — alias-consistent model id."""

from __future__ import annotations

import json

import pytest

from backend.App.integrations.application.ui_gateway import _local_models_payload


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("AILOUROS_MODELS_DIR", "AILOUROS_DEFAULT_MODELS_MANIFEST", "SWARM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    yield


def test_no_models_dir_returns_empty(monkeypatch):
    payload = _local_models_payload()
    assert payload == {"ok": True, "models": []}


def test_empty_directory_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    assert _local_models_payload() == {"ok": True, "models": []}


def test_single_gguf_returns_alias_with_stem_as_source(monkeypatch, tmp_path):
    (tmp_path / "gemma-4-e4b-it-q4-k-m.gguf").write_bytes(b"x")
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    payload = _local_models_payload()
    assert payload["ok"] is True
    assert len(payload["models"]) == 1
    item = payload["models"][0]
    assert item["id"] == "local-default"
    assert item["source_file"] == "gemma-4-e4b-it-q4-k-m"
    assert item["label"] == "gemma-4-e4b-it-q4-k-m"  # falls back to stem when no manifest


def test_manifest_label_used_when_present(monkeypatch, tmp_path):
    (tmp_path / "gemma-4-e4b-it-q4-k-m.gguf").write_bytes(b"x")
    manifest = tmp_path / "default-models.json"
    manifest.write_text(
        json.dumps(
            {
                "default_model_id": "gemma-4-e4b-it-q4-k-m",
                "models": [
                    {
                        "id": "gemma-4-e4b-it-q4-k-m",
                        "label": "Gemma 4 E4B Instruct (Q4_K_M)",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AILOUROS_DEFAULT_MODELS_MANIFEST", str(manifest))
    payload = _local_models_payload()
    item = payload["models"][0]
    assert item["id"] == "local-default"
    assert item["label"] == "Gemma 4 E4B Instruct (Q4_K_M)"
    assert item["source_file"] == "gemma-4-e4b-it-q4-k-m"


def test_swarm_model_env_overrides_alias(monkeypatch, tmp_path):
    (tmp_path / "model-x.gguf").write_bytes(b"x")
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("SWARM_MODEL", "alias-y")
    payload = _local_models_payload()
    assert payload["models"][0]["id"] == "alias-y"


def test_default_model_picked_from_manifest_among_multiple(monkeypatch, tmp_path):
    (tmp_path / "alpha.gguf").write_bytes(b"x")
    (tmp_path / "beta.gguf").write_bytes(b"x")
    manifest = tmp_path / "default-models.json"
    manifest.write_text(
        json.dumps(
            {
                "default_model_id": "beta",
                "models": [
                    {"id": "alpha", "label": "Alpha"},
                    {"id": "beta", "label": "Beta"},
                ],
            }
        )
    )
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AILOUROS_DEFAULT_MODELS_MANIFEST", str(manifest))
    payload = _local_models_payload()
    item = payload["models"][0]
    assert item["source_file"] == "beta"
    assert item["label"] == "Beta"


def test_falls_back_to_first_when_manifest_default_missing(monkeypatch, tmp_path):
    (tmp_path / "alpha.gguf").write_bytes(b"x")
    (tmp_path / "beta.gguf").write_bytes(b"x")
    manifest = tmp_path / "default-models.json"
    manifest.write_text(
        json.dumps(
            {
                "default_model_id": "missing",
                "models": [{"id": "alpha", "label": "Alpha"}],
            }
        )
    )
    monkeypatch.setenv("AILOUROS_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("AILOUROS_DEFAULT_MODELS_MANIFEST", str(manifest))
    payload = _local_models_payload()
    item = payload["models"][0]
    assert item["source_file"] == "alpha"
    assert item["label"] == "Alpha"
