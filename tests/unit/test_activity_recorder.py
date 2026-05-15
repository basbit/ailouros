from __future__ import annotations

import json

import pytest

from backend.App.shared.infrastructure import activity_recorder


@pytest.fixture()
def task_id(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path))
    token = activity_recorder.set_active_task("task-1")
    yield "task-1"
    activity_recorder.reset_active_task(token)


def test_record_writes_jsonl(task_id, tmp_path):
    entry = activity_recorder.record(
        "web_searches",
        {"provider": "tavily", "query": "openai", "hit_count": 3},
    )
    assert entry is not None
    activity_path = tmp_path / "task-1" / "activity" / "web_searches.jsonl"
    assert activity_path.is_file()
    line = activity_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["provider"] == "tavily"
    assert parsed["task_id"] == "task-1"
    assert "ts" in parsed


def test_record_without_active_task_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path))
    token = activity_recorder.set_active_task(None)
    try:
        assert activity_recorder.record("qdrant_ops", {"op": "search"}) is None
    finally:
        activity_recorder.reset_active_task(token)


def test_record_rejects_unknown_channel(task_id):
    with pytest.raises(ValueError):
        activity_recorder.record("bogus_channel", {"x": 1})


def test_read_tail_returns_last_n(task_id):
    for index in range(5):
        activity_recorder.record(
            "mcp_calls",
            {"server": "workspace", "tool": "read", "args": {"i": index}},
        )
    tail = activity_recorder.read_tail("task-1", "mcp_calls", limit=2)
    assert len(tail) == 2
    assert tail[-1]["args"]["i"] == 4


def test_read_tail_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACTS_DIR", str(tmp_path))
    assert activity_recorder.read_tail("nonexistent", "mcp_calls") == []


def test_record_truncates_long_strings(task_id, tmp_path):
    long = "x" * 5000
    activity_recorder.record("mcp_calls", {"server": "w", "tool": "t", "result": long})
    line = (tmp_path / "task-1" / "activity" / "mcp_calls.jsonl").read_text(
        encoding="utf-8"
    ).strip()
    parsed = json.loads(line)
    assert parsed["result"].endswith("…")
    assert len(parsed["result"]) < 600
