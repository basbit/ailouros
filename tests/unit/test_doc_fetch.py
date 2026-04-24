"""SWARM_DOC_FETCH — кэш в artifacts и зеркало в workspace/.swarm/doc_fetch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from backend.App.integrations.infrastructure.doc_fetch import doc_fetch_enabled, run_doc_fetch_if_enabled


def test_doc_fetch_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_DOC_FETCH", "0")
    assert not doc_fetch_enabled()
    assert run_doc_fetch_if_enabled({}) == []


def test_doc_fetch_writes_artifacts_and_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DOC_FETCH", "1")
    monkeypatch.delenv("SWARM_ARTIFACTS_DIR", raising=False)
    art = tmp_path / "artifacts"
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(art))

    ws = tmp_path / "ws"
    ws.mkdir()

    class Resp:
        status_code = 200
        content = b"hello documentation"
        headers = {"content-type": "text/plain"}

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kwargs):
            return Resp()

    monkeypatch.setattr("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", Client)

    cfg = {
        "swarm": {
            "documentation_sources": [
                {"url": "https://example.com/docs", "title": "Docs"},
            ]
        }
    }
    man = run_doc_fetch_if_enabled(
        cfg,
        workspace_root=str(ws),
        task_id="t1",
    )
    assert len(man) == 1
    assert man[0].get("ok") is True
    d = art / "t1" / "doc_fetch"
    subdirs = list(d.iterdir())
    assert len(subdirs) == 1
    key_dir = subdirs[0]
    assert (key_dir / "body.txt").read_text(encoding="utf-8") == "hello documentation"
    meta = json.loads((key_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == "https://example.com/docs"
    wsf = ws / ".swarm" / "doc_fetch" / key_dir.name / "body.txt"
    assert wsf.read_text(encoding="utf-8") == "hello documentation"


def test_doc_fetch_skips_loopback_without_httpx(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_DOC_FETCH", "1")
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path / "a"))

    mock_client = MagicMock()
    monkeypatch.setattr("backend.App.integrations.infrastructure.doc_fetch.httpx.Client", mock_client)

    cfg = {
        "swarm": {
            "documentation_sources": [{"url": "http://127.0.0.1/secret", "title": "x"}],
        }
    }
    man = run_doc_fetch_if_enabled(cfg, workspace_root="", task_id="")
    assert len(man) == 1
    assert man[0].get("ok") is False
    assert "blocked" in (man[0].get("error") or "")
    mock_client.assert_not_called()
