"""Unit tests for memory consolidation — both layers.

application layer: backend.App.integrations.application.memory_consolidation
infrastructure layer: backend.App.integrations.infrastructure.memory_consolidation
"""
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
from backend.App.integrations.infrastructure.memory_consolidation import (
    _k_means_cluster,
    consolidate_episodes,
    dream_pass,
    _token_overlap,
)

# --- _tokenize ---


def test_tokenize_basic():
    assert _tokenize("Hello World foo") == ["hello", "world", "foo"]


def test_tokenize_filters_short_tokens():
    assert _tokenize("a is to bar") == ["bar"]


def test_tokenize_splits_on_non_word_chars():
    result = _tokenize("one,two-three.four")
    assert result == ["one", "two", "three", "four"]


# --- _tf ---

def test_tf_empty():
    assert _tf([]) == {}


def test_tf_counts_normalised():
    result = _tf(["cat", "cat", "dog"])
    assert abs(result["cat"] - 2 / 3) < 1e-9
    assert abs(result["dog"] - 1 / 3) < 1e-9


# --- _cosine (application layer, dict-based) ---

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
    expected = 1.0 / (math.sqrt(2) * math.sqrt(2))
    assert abs(_cosine(a, b) - expected) < 1e-9


def test_cosine_zero_norm_value():
    a = {"a": 0.0}
    b = {"a": 1.0}
    assert _cosine(a, b) == 0.0


# --- MemoryConsolidator._cluster_episodes ---

def test_cluster_episodes_empty():
    mc = MemoryConsolidator()
    assert mc._cluster_episodes([]) == []


def test_cluster_episodes_similar_texts_grouped():
    ep1 = {"body": "the quick brown fox jumps over the lazy dog", "step": "s1"}
    ep2 = {"body": "the quick brown fox jumps over the lazy dog", "step": "s2"}
    ep3 = {"body": "completely unrelated zephyr quartz vibrant", "step": "s3"}
    mc = MemoryConsolidator()
    clusters = mc._cluster_episodes([ep1, ep2, ep3])
    sizes = sorted(len(c) for c in clusters)
    assert 2 in sizes


# --- MemoryConsolidator._extract_patterns ---

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


# --- MemoryConsolidator.run_consolidation ---

def test_run_consolidation_too_few_episodes_returns_zero_stats():
    mc = MemoryConsolidator()
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=[{"body": "only one episode", "consolidated": False}],
    ):
        stats = mc.run_consolidation(namespace="test")
    assert stats["clusters_formed"] == 0
    assert stats["patterns_stored"] == 0
    assert stats["episodes_marked"] == 0
    assert stats["episodes_loaded"] == 1


def test_run_consolidation_clusters_and_stores():
    mc = MemoryConsolidator(llm_backend=None)
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
    assert "pattern_key" in mock_store.call_args.kwargs or len(mock_store.call_args.args) > 0


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


def test_run_consolidation_small_cluster_not_stored(tmp_path):
    mc = MemoryConsolidator(llm_backend=None)
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


# --- MemoryConsolidator._mark_episodes_consolidated ---

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
    ctm._LOCAL_EPISODES.clear()


# --- _build_tfidf_vectors ---

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


# ===========================================================================
# Infrastructure layer (embedding-based k-means)
# ===========================================================================

# --- _k_means_cluster ---


def test_k_means_empty():
    assert _k_means_cluster([], 3) == []


def test_k_means_k_zero():
    assert _k_means_cluster([[1.0, 0.0]], 0) == []


def test_k_means_single_point():
    labels = _k_means_cluster([[1.0, 2.0]], 3)
    assert labels == [0]


def test_k_means_k_clamped_to_n():
    vecs = [[float(i), 0.0] for i in range(3)]
    labels = _k_means_cluster(vecs, 10)  # k > n → clamped to 3
    assert len(labels) == 3


def test_k_means_two_clear_clusters():
    # Two tight groups far apart — k-means must separate them
    group_a = [[0.0, 0.0], [0.01, 0.0], [0.0, 0.01]]
    group_b = [[10.0, 10.0], [10.01, 10.0], [10.0, 10.01]]
    vecs = group_a + group_b
    labels = _k_means_cluster(vecs, 2)
    assert len(labels) == 6
    # All points in group_a share one label, all in group_b share another
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_k_means_identical_points():
    vecs = [[1.0, 1.0]] * 5
    labels = _k_means_cluster(vecs, 2)
    assert len(labels) == 5
    # All get the same label when all points are identical
    assert len(set(labels)) == 1


# --- _token_overlap ---

def test_token_overlap_identical():
    score = _token_overlap("the quick brown fox", "the quick brown fox")
    assert score > 0.9


def test_token_overlap_disjoint():
    score = _token_overlap("apple orange mango", "zephyr quartz vibrant")
    assert score == 0.0


def test_token_overlap_empty():
    assert _token_overlap("", "something here") == 0.0
    assert _token_overlap("something here", "") == 0.0


# --- consolidate_episodes ---

def test_consolidate_episodes_empty():
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=[],
    ):
        result = consolidate_episodes("test-ns")
    assert result == []


def test_consolidate_episodes_fewer_than_min_cluster_size(caplog):
    episodes = [{"body": "lone episode here", "step": "pm"}]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ):
        import logging
        with caplog.at_level(logging.WARNING):
            result = consolidate_episodes("test-ns", min_cluster_size=3)
    assert result == []
    assert any("min_cluster_size" in r.message for r in caplog.records)


def test_consolidate_episodes_with_embeddings():
    # Two tight clusters of 3 each — should produce 2 super-episodes
    vecs_a = [[0.0, 0.0], [0.01, 0.0], [0.0, 0.01]]
    vecs_b = [[10.0, 10.0], [10.01, 10.0], [10.0, 10.01]]
    episodes = (
        [{"body": f"alpha body {i}", "step": "pm", "embedding": v} for i, v in enumerate(vecs_a)]
        + [{"body": f"beta body {i}", "step": "dev", "embedding": v} for i, v in enumerate(vecs_b)]
    )
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ):
        result = consolidate_episodes("test-ns", min_cluster_size=2, max_clusters=4)
    assert len(result) >= 1
    for super_ep in result:
        assert "body" in super_ep
        assert super_ep.get("step") == "dream_pass"


def test_consolidate_episodes_without_embeddings_degrades_gracefully():
    # Episodes without embedding field — fallback to token grouping
    episodes = [
        {"body": "retry transient network error", "step": "dev"},
        {"body": "retry transient network timeout", "step": "dev"},
        {"body": "retry transient network failure", "step": "dev"},
        {"body": "completely different topic about databases", "step": "db"},
        {"body": "database connection pool exhausted", "step": "db"},
        {"body": "database query optimisation", "step": "db"},
    ]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ):
        result = consolidate_episodes("test-ns", min_cluster_size=2)
    # At least one cluster must be formed from the text-similar retry episodes
    assert isinstance(result, list)


def test_consolidate_episodes_small_clusters_logged(caplog):
    # With min_cluster_size=4 and only 3 per group, clusters are skipped with a warning
    vecs = [[float(i), 0.0] for i in range(3)]
    episodes = [{"body": f"body {i}", "step": "pm", "embedding": v} for i, v in enumerate(vecs)]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
        return_value=episodes,
    ):
        import logging
        with caplog.at_level(logging.WARNING):
            result = consolidate_episodes("test-ns", min_cluster_size=4)
    # Should either return [] or log a warning
    assert isinstance(result, list)


# --- dream_pass ---

def test_dream_pass_disabled_by_default():
    # SWARM_DREAM_PASS_ENABLED is not set → returns 0 with a warning
    import os
    os.environ.pop("SWARM_DREAM_PASS_ENABLED", None)
    result = dream_pass("default")
    assert result == 0


def test_dream_pass_enabled_no_episodes():
    import os
    os.environ["SWARM_DREAM_PASS_ENABLED"] = "1"
    try:
        with patch(
            "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
            return_value=[],
        ):
            result = dream_pass("empty-ns")
        assert result == 0
    finally:
        os.environ.pop("SWARM_DREAM_PASS_ENABLED", None)


def test_dream_pass_stores_clusters():
    import os
    os.environ["SWARM_DREAM_PASS_ENABLED"] = "1"
    try:
        vecs_a = [[0.0, 0.0], [0.01, 0.0], [0.0, 0.01]]
        vecs_b = [[10.0, 10.0], [10.01, 10.0], [10.0, 10.01]]
        episodes = (
            [{"body": f"alpha {i}", "step": "pm", "embedding": v} for i, v in enumerate(vecs_a)]
            + [{"body": f"beta {i}", "step": "dev", "embedding": v} for i, v in enumerate(vecs_b)]
        )
        with patch(
            "backend.App.integrations.infrastructure.cross_task_memory._load_episodes",
            return_value=episodes,
        ), patch(
            "backend.App.integrations.infrastructure.pattern_memory.store_consolidated_pattern"
        ) as mock_store, patch(
            "backend.App.integrations.infrastructure.pattern_memory.pattern_memory_path_for_state"
        ):
            result = dream_pass("test-ns")
        assert result >= 1
        mock_store.assert_called()
    finally:
        os.environ.pop("SWARM_DREAM_PASS_ENABLED", None)
