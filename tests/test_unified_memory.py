"""Tests for H-9 UnifiedMemorySearch (unified_memory.py)."""
from __future__ import annotations

from unittest.mock import patch

from backend.App.integrations.infrastructure.unified_memory import (
    unified_memory_search_enabled,
    search_memory,
    format_unified_memory_block,
    _MemHit,
    _query_pattern_memory,
    _query_cross_task_memory,
    _query_wiki,
)


# ---------------------------------------------------------------------------
# unified_memory_search_enabled
# ---------------------------------------------------------------------------

def test_enabled_default(monkeypatch):
    monkeypatch.delenv("SWARM_UNIFIED_MEMORY_SEARCH", raising=False)
    assert unified_memory_search_enabled() is True


def test_disabled_by_0(monkeypatch):
    monkeypatch.setenv("SWARM_UNIFIED_MEMORY_SEARCH", "0")
    assert unified_memory_search_enabled() is False


def test_enabled_explicit_1(monkeypatch):
    monkeypatch.setenv("SWARM_UNIFIED_MEMORY_SEARCH", "1")
    assert unified_memory_search_enabled() is True


# ---------------------------------------------------------------------------
# _MemHit
# ---------------------------------------------------------------------------

def test_mem_hit_fields():
    h = _MemHit(source="pattern", label="key1", body="some text", score=3.5)
    assert h.source == "pattern"
    assert h.label == "key1"
    assert h.body == "some text"
    assert h.score == 3.5


# ---------------------------------------------------------------------------
# Backend adapters — each must never raise even when the backend fails
# ---------------------------------------------------------------------------

def test_query_pattern_memory_graceful_on_error():
    """Should return [] when pattern_memory raises."""
    state: dict = {}
    with patch(
        "backend.App.integrations.infrastructure.pattern_memory.search_patterns",
        side_effect=RuntimeError("store unavailable"),
    ):
        result = _query_pattern_memory(state, "query", 4)
    assert result == []


def test_query_cross_task_memory_graceful_on_error():
    state: dict = {}
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory.search_episodes",
        side_effect=RuntimeError("redis down"),
    ):
        result = _query_cross_task_memory(state, "query", 4)
    assert result == []


def test_query_wiki_returns_empty_without_wiki_root():
    """No wiki_root in state → empty result without error."""
    state: dict = {}
    result = _query_wiki(state, "query", 4)
    assert result == []


def test_query_pattern_memory_converts_hits():
    state: dict = {}
    fake_hits = [("pattern-key", "pattern value text", 4.5)]
    with patch(
        "backend.App.integrations.infrastructure.pattern_memory.search_patterns",
        return_value=fake_hits,
    ):
        result = _query_pattern_memory(state, "query", 4)
    assert len(result) == 1
    assert result[0].source == "pattern"
    assert result[0].label == "pattern-key"
    assert result[0].body == "pattern value text"
    assert result[0].score == 4.5


def test_query_cross_task_memory_converts_hits():
    state: dict = {}
    fake_episode = {"step": "dev", "task_id": "abc123xyz", "body": "episode body text"}
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory.search_episodes",
        return_value=[(fake_episode, 2.8)],
    ):
        result = _query_cross_task_memory(state, "query", 4)
    assert len(result) == 1
    assert result[0].source == "episode"
    assert result[0].score == 2.8
    assert "dev" in result[0].label


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------

def test_search_memory_returns_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_UNIFIED_MEMORY_SEARCH", "0")
    result = search_memory({}, "query")
    assert result == []


def test_search_memory_sorts_by_score_descending():
    state: dict = {}
    hits_a = [_MemHit("pattern", "k1", "body1", 1.0)]
    hits_b = [_MemHit("episode", "e1", "body2", 5.0)]
    hits_c: list = []
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=hits_a),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=hits_b),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=hits_c),
    ):
        result = search_memory(state, "query")
    assert result[0].score == 5.0
    assert result[1].score == 1.0


def test_search_memory_merges_all_backends():
    state: dict = {}
    hits_a = [_MemHit("pattern", "k1", "b1", 3.0)]
    hits_b = [_MemHit("episode", "e1", "b2", 2.0)]
    hits_c = [_MemHit("wiki", "w1", "b3", 1.0)]
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=hits_a),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=hits_b),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=hits_c),
    ):
        result = search_memory(state, "query")
    assert len(result) == 3
    sources = {h.source for h in result}
    assert sources == {"pattern", "episode", "wiki"}


# ---------------------------------------------------------------------------
# format_unified_memory_block
# ---------------------------------------------------------------------------

def test_format_unified_memory_block_empty_when_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_UNIFIED_MEMORY_SEARCH", "0")
    result = format_unified_memory_block({}, "query")
    assert result == ""


def test_format_unified_memory_block_empty_when_no_hits():
    state: dict = {}
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=[]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=[]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=[]),
    ):
        result = format_unified_memory_block(state, "query")
    assert result == ""


def test_format_unified_memory_block_contains_hit_body():
    state: dict = {}
    hits = [_MemHit("pattern", "mykey", "important pattern content", 3.0)]
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=hits),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=[]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=[]),
    ):
        result = format_unified_memory_block(state, "query")
    assert "important pattern content" in result
    assert "mykey" in result


def test_format_unified_memory_block_respects_max_chars():
    state: dict = {}
    # Generate a hit with body longer than the budget
    long_body = "x" * 10000
    hits = [_MemHit("pattern", "k1", long_body, 5.0)]
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=hits),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=[]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=[]),
    ):
        result = format_unified_memory_block(state, "query", max_chars=500)
    # The block should either be empty (body alone exceeds budget) or within budget
    assert len(result) <= 600  # some overhead for headers


def test_format_unified_memory_block_zero_max_chars():
    state: dict = {}
    result = format_unified_memory_block(state, "query", max_chars=0)
    assert result == ""


def test_format_unified_memory_block_labels_source():
    state: dict = {}
    hits = [
        _MemHit("pattern", "pk", "pattern body", 4.0),
        _MemHit("episode", "ek", "episode body", 3.0),
        _MemHit("wiki", "wk", "wiki body", 2.0),
    ]
    with (
        patch("backend.App.integrations.infrastructure.unified_memory._query_pattern_memory", return_value=[hits[0]]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_cross_task_memory", return_value=[hits[1]]),
        patch("backend.App.integrations.infrastructure.unified_memory._query_wiki", return_value=[hits[2]]),
    ):
        result = format_unified_memory_block(state, "query")
    assert "Pattern" in result
    assert "Episode" in result
    assert "Wiki" in result
