"""embedding_service: provider selection, cache, null fallback."""
from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.embedding_service import (
    NullEmbeddingProvider,
    _CachedProvider,
    _auto_choose_provider_id,
    _make_provider,
    get_embedding_provider,
    reset_embedding_provider,
)


def test_null_provider_returns_empty_vectors():
    p = NullEmbeddingProvider()
    assert p.name == "null"
    assert p.dim == 0
    assert p.embed([]) == []
    assert p.embed(["foo", "bar"]) == [[], []]


def test_make_provider_unknown_raises():
    with pytest.raises(ValueError, match="unknown SWARM_EMBEDDING_PROVIDER"):
        _make_provider("banana")


def test_make_provider_null():
    assert isinstance(_make_provider("null"), NullEmbeddingProvider)


def test_cached_provider_empty_input(monkeypatch):
    """Empty input must not touch the inner provider."""
    inner_calls: list[list[str]] = []

    class _Recorder:
        name = "rec"
        dim = 3

        def embed(self, texts):
            inner_calls.append(list(texts))
            return [[1.0, 2.0, 3.0] for _ in texts]

    wrapped = _CachedProvider(_Recorder(), capacity=4)
    assert wrapped.embed([]) == []
    assert inner_calls == []


def test_cached_provider_caches_across_calls():
    """Second call for the same text is served from cache."""
    inner_calls: list[list[str]] = []

    class _Recorder:
        name = "rec"
        dim = 2

        def embed(self, texts):
            inner_calls.append(list(texts))
            return [[float(ord(t[0])), 0.0] for t in texts]

    wrapped = _CachedProvider(_Recorder(), capacity=4)
    first = wrapped.embed(["a", "b"])
    second = wrapped.embed(["a", "b"])
    assert first == second == [[97.0, 0.0], [98.0, 0.0]]
    # Only first call reached the inner provider.
    assert inner_calls == [["a", "b"]]


def test_cached_provider_mixed_hits_and_misses():
    inner_calls: list[list[str]] = []

    class _Recorder:
        name = "rec"
        dim = 1

        def embed(self, texts):
            inner_calls.append(list(texts))
            return [[1.0] for _ in texts]

    wrapped = _CachedProvider(_Recorder(), capacity=4)
    wrapped.embed(["a"])
    wrapped.embed(["a", "b"])
    # Second call only asked inner for "b".
    assert inner_calls == [["a"], ["b"]]


def test_cached_provider_disabled_when_capacity_zero():
    inner_calls: list[list[str]] = []

    class _Recorder:
        name = "rec"
        dim = 1

        def embed(self, texts):
            inner_calls.append(list(texts))
            return [[1.0] for _ in texts]

    wrapped = _CachedProvider(_Recorder(), capacity=0)
    wrapped.embed(["a"])
    wrapped.embed(["a"])
    # Every call reaches inner when cache is off.
    assert inner_calls == [["a"], ["a"]]


def test_cached_provider_eviction():
    """When capacity is exceeded, oldest entries are evicted (FIFO)."""
    inner_calls: list[list[str]] = []

    class _Recorder:
        name = "rec"
        dim = 1

        def embed(self, texts):
            inner_calls.append(list(texts))
            return [[1.0] for _ in texts]

    wrapped = _CachedProvider(_Recorder(), capacity=2)
    wrapped.embed(["a", "b"])
    wrapped.embed(["c"])  # evicts "a"
    wrapped.embed(["a"])  # must re-embed, not cached anymore
    assert inner_calls == [["a", "b"], ["c"], ["a"]]


def test_get_embedding_provider_returns_singleton(monkeypatch):
    monkeypatch.setenv("SWARM_EMBEDDING_PROVIDER", "null")
    reset_embedding_provider()
    p1 = get_embedding_provider()
    p2 = get_embedding_provider()
    assert p1 is p2
    assert "null" in p1.name


def test_get_embedding_provider_falls_back_on_init_failure(monkeypatch):
    """If the chosen provider raises during init, fall back to null."""
    monkeypatch.setenv("SWARM_EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("SWARM_EMBEDDING_MODEL", "__nonexistent__")
    reset_embedding_provider()
    # sentence_transformers itself may or may not be installed; either way,
    # get_embedding_provider must not raise — worst case we land on null.
    p = get_embedding_provider()
    assert hasattr(p, "embed")
    assert p.embed(["hello"]) is not None


def test_auto_choose_returns_known_value(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    choice = _auto_choose_provider_id()
    assert choice in {"local", "openai", "null"}


def test_auto_choose_prefers_openai_when_sentence_transformers_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name.startswith("sentence_transformers"):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    assert _auto_choose_provider_id() == "openai"


def test_auto_choose_null_when_nothing_available(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name.startswith("sentence_transformers"):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert _auto_choose_provider_id() == "null"


def test_reset_clears_singleton(monkeypatch):
    monkeypatch.setenv("SWARM_EMBEDDING_PROVIDER", "null")
    reset_embedding_provider()
    p1 = get_embedding_provider()
    reset_embedding_provider()
    p2 = get_embedding_provider()
    assert p1 is not p2
