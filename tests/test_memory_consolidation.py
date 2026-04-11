"""Unit tests for MemoryConsolidator and its pure helpers."""
from __future__ import annotations

import math
from unittest.mock import patch, MagicMock

from backend.App.integrations.application.memory_consolidation import (
    MemoryConsolidator,
    _build_tfidf_vectors,
    _cosine,
    _tf,
    _tokenize,
)

# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


def test_tokenize_basic():
    assert _tokenize("Hello World foo") == ["hello", "world", "foo"]


def test_tokenize_filters_short_tokens():
    # "a", "is", "to" are all < 3 chars and must be dropped
    assert _tokenize("a is to bar") == ["bar"]


def test_tokenize_splits_on_non_word_chars():
    # punctuation and hyphens are treated as delimiters
    result = _tokenize("one,two-three.four")
    assert result == ["one", "two", "three", "four"]


# ---------------------------------------------------------------------------
# _tf
# ---------------------------------------------------------------------------

def test_tf_empty():
    assert _tf([]) == {}


def test_tf_counts_normalised():
    result = _tf(["cat", "cat", "dog"])
    assert abs(result["cat"] - 2 / 3) < 1e-9
    assert abs(result["dog"] - 1 / 3) < 1e-9


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

def test_cosine_empty_vector_returns_zero():
    assert _cosine({}, {"a": 1.0}) == 0.0
    assert _cosine({"a": 1.0}, {}) == 0.0
    assert _cosine({}, {}) == 0.0


def test_cosine_identical_vectors():
    v = {"hello": 0.5, "world": 0.5}
    result = _cosine(v, v)
    assert abs(result - 1.0) < 1e-9


def test_cosine_orthogonal_vectors():
    a = {"alpha": 1.0}
    b = {"beta": 1.0}
    assert _cosine(a, b) == 0.0


def test_cosine_partial_overlap():
    a = {"x": 1.0, "y": 1.0}
    b = {"x": 1.0, "z": 1.0}
    # dot = 1, |a|=sqrt(2), |b|=sqrt(2) => 1/2 = 0.5
    expected = 1.0 / (math.sqrt(2) * math.sqrt(2))
    assert abs(_cosine(a, b) - expected) < 1e-9


# ---------------------------------------------------------------------------
# MemoryConsolidator._cluster_episodes
# ---------------------------------------------------------------------------

def test_cluster_episodes_empty():
    mc = MemoryConsolidator()
    assert mc._cluster_episodes([]) == []


def test_cluster_episodes_similar_texts_grouped():
    # Two nearly identical episodes should land in the same cluster
    ep1 = {"body": "the quick brown fox jumps over the lazy dog", "step": "s1"}
    ep2 = {"body": "the quick brown fox jumps over the lazy dog", "step": "s2"}
    ep3 = {"body": "completely unrelated zephyr quartz vibrant", "step": "s3"}

    mc = MemoryConsolidator()
    clusters = mc._cluster_episodes([ep1, ep2, ep3])

    # Find the cluster that contains ep1 and ep2 — they must be together
    sizes = sorted(len(c) for c in clusters)
    # ep1+ep2 should be merged; ep3 is a singleton
    assert 2 in sizes


# ---------------------------------------------------------------------------
# MemoryConsolidator._extract_patterns — no LLM fallback
# ---------------------------------------------------------------------------

def test_extract_patterns_no_llm():
    mc = MemoryConsolidator(llm_backend=None)
    cluster = [
        {"body": "authentication token refresh strategy used here", "step": "auth"},
        {"body": "authentication token refresh strategy applied again", "step": "auth"},
        {"body": "authentication token refresh rate limit", "step": "rate"},
    ]
    pattern_text, pattern_key = mc._extract_patterns(cluster)

    assert "authentication" in pattern_text.lower() or "token" in pattern_text.lower()
    assert pattern_key.startswith("dream:")
    assert "auth" in pattern_key


# ---------------------------------------------------------------------------
# MemoryConsolidator.run_consolidation — below _MIN_CLUSTER_SIZE
# ---------------------------------------------------------------------------

def test_run_consolidation_too_few_episodes_returns_zero_stats():
    mc = MemoryConsolidator()
    # Patch _load_episodes to return 1 pending episode (below default MIN=3)
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=[{"body": "only one episode", "consolidated": False}],
    ):
        stats = mc.run_consolidation(namespace="test")

    assert stats["clusters_formed"] == 0
    assert stats["patterns_stored"] == 0
    assert stats["episodes_marked"] == 0
    assert stats["episodes_loaded"] == 1


# ---------------------------------------------------------------------------
# MemoryConsolidator.run_consolidation — clusters and stores patterns
# ---------------------------------------------------------------------------

def test_run_consolidation_clusters_and_stores():
    mc = MemoryConsolidator(llm_backend=None)

    # Three identical episodes — well above MIN_CLUSTER_SIZE=3 and sure to cluster
    shared_body = (
        "retry logic for transient network errors with exponential backoff strategy"
    )
    episodes = [
        {"body": shared_body, "step": "net", "task_id": f"t{i}", "consolidated": False}
        for i in range(4)
    ]

    with (
        patch(
            "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
            return_value=episodes,
        ),
        patch(
            "backend.App.integrations.infrastructure.pattern_memory.store_consolidated_pattern"
        ) as mock_store,
        patch.object(mc, "_mark_episodes_consolidated"),
    ):
        stats = mc.run_consolidation(namespace="test")

    assert stats["clusters_formed"] >= 1
    assert stats["patterns_stored"] >= 1
    mock_store.assert_called()
    # store_consolidated_pattern is called with keyword args including pattern_key
    assert "pattern_key" in mock_store.call_args.kwargs or len(mock_store.call_args.args) > 0


# ---------------------------------------------------------------------------
# _build_tfidf_vectors
# ---------------------------------------------------------------------------

def test_build_tfidf_vectors_empty():
    assert _build_tfidf_vectors([]) == []


def test_build_tfidf_vectors_single_doc():
    docs = [["hello", "world", "hello"]]
    result = _build_tfidf_vectors(docs)
    assert len(result) == 1
    assert "hello" in result[0]
    assert "world" in result[0]


def test_build_tfidf_vectors_multiple_docs():
    docs = [["foo", "bar"], ["foo", "baz"], ["qux"]]
    result = _build_tfidf_vectors(docs)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# _cosine — zero-norm edge case
# ---------------------------------------------------------------------------

def test_cosine_zero_norm_value():
    """Vector with all-zero values → norm=0 → returns 0.0"""
    a = {"a": 0.0}
    b = {"a": 1.0}
    assert _cosine(a, b) == 0.0


# ---------------------------------------------------------------------------
# MemoryConsolidator._extract_patterns — with LLM
# ---------------------------------------------------------------------------

def test_extract_patterns_with_llm():
    mock_llm = MagicMock()
    mock_llm.chat.return_value = ("Extracted pattern: use retry logic", {})
    mc = MemoryConsolidator(llm_backend=mock_llm)
    cluster = [
        {"body": "retry logic for network errors", "step": "dev"},
        {"body": "retry logic for network timeouts", "step": "dev"},
    ]
    text, key = mc._extract_patterns(cluster)
    assert text == "Extracted pattern: use retry logic"
    mock_llm.chat.assert_called_once()


# ---------------------------------------------------------------------------
# MemoryConsolidator._mark_episodes_consolidated — local
# ---------------------------------------------------------------------------

def test_mark_episodes_consolidated_marks_matching():
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    ns = "mark-test-ns"
    episodes = [
        {"step": "pm", "body": "first episode body text here", "consolidated": False},
        {"step": "dev", "body": "second episode different body", "consolidated": False},
    ]
    ctm._LOCAL_EPISODES[ns] = episodes

    mc = MemoryConsolidator()
    cluster = [episodes[0]]

    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=None,
    ):
        mc._mark_episodes_consolidated(ns, cluster)

    assert episodes[0]["consolidated"] is True
    assert episodes[1].get("consolidated", False) is False
    ctm._LOCAL_EPISODES.clear()


def test_mark_episodes_consolidated_no_local_namespace():
    import backend.App.integrations.infrastructure.cross_task_memory as ctm
    ctm._LOCAL_EPISODES.clear()

    mc = MemoryConsolidator()
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=None,
    ):
        mc._mark_episodes_consolidated("nonexistent-ns", [{"body": "stuff", "step": "pm"}])
    # No crash
    ctm._LOCAL_EPISODES.clear()


# ---------------------------------------------------------------------------
# MemoryConsolidator.run_consolidation — already consolidated episodes skip
# ---------------------------------------------------------------------------

def test_run_consolidation_all_already_consolidated():
    mc = MemoryConsolidator()
    episodes = [
        {"body": "old", "step": "pm", "consolidated": True},
        {"body": "old2", "step": "pm", "consolidated": True},
        {"body": "old3", "step": "pm", "consolidated": True},
    ]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ):
        stats = mc.run_consolidation(namespace="test")

    assert stats["patterns_stored"] == 0
    assert stats["episodes_marked"] == 0


# ---------------------------------------------------------------------------
# MemoryConsolidator.run_consolidation — cluster below min size still skipped
# ---------------------------------------------------------------------------

def test_run_consolidation_small_cluster_not_stored(tmp_path):
    """Clusters smaller than _MIN_CLUSTER_SIZE are skipped."""
    mc = MemoryConsolidator(llm_backend=None)

    # Only 2 episodes but _MIN_CLUSTER_SIZE=3 → skip
    episodes = [
        {"body": "retry logic transient network errors", "step": "dev", "consolidated": False},
        {"body": "retry logic transient network timeouts", "step": "dev", "consolidated": False},
    ]

    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ), patch(
        "backend.App.integrations.application.memory_consolidation._MIN_CLUSTER_SIZE",
        3,
    ):
        stats = mc.run_consolidation(namespace="test")

    assert stats["patterns_stored"] == 0
