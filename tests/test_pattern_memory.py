"""integrations.pattern_memory — поиск и запись."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.integrations.infrastructure.pattern_memory import (
    format_pattern_memory_block,
    pattern_memory_enabled,
    search_patterns,
    store_pattern,
)


def test_store_and_search(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "mem.json"
    store_pattern(p, "default", "auth jwt", "use HS256; rotate keys weekly", merge=False)
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(p)}}}
    assert pattern_memory_enabled(state)
    hits = search_patterns(state, "jwt authentication", limit=3)
    assert hits
    assert "auth jwt" in hits[0][0]
    block = format_pattern_memory_block(state, "jwt")
    assert "HS256" in block


def test_disabled_with_explicit_off(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "0")
    state = {"agent_config": {"swarm": {}}}
    assert not pattern_memory_enabled(state)
    assert search_patterns(state, "anything") == []


# ---------------------------------------------------------------------------
# pattern_memory_enabled — various paths
# ---------------------------------------------------------------------------

def test_pattern_memory_enabled_via_env(monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "1")
    assert pattern_memory_enabled({}) is True


def test_pattern_memory_enabled_no_agent_config(monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "0")
    assert pattern_memory_enabled({}) is False


def test_pattern_memory_enabled_agent_config_not_dict(monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "0")
    state = {"agent_config": "not a dict"}
    assert pattern_memory_enabled(state) is False


def test_pattern_memory_enabled_swarm_not_dict(monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "0")
    state = {"agent_config": {"swarm": "not a dict"}}
    assert pattern_memory_enabled(state) is False


def test_pattern_memory_enabled_true_bool():
    state = {"agent_config": {"swarm": {"pattern_memory": True}}}
    assert pattern_memory_enabled(state) is True


def test_pattern_memory_enabled_false_bool():
    state = {"agent_config": {"swarm": {"pattern_memory": False}}}
    assert pattern_memory_enabled(state) is False


# ---------------------------------------------------------------------------
# pattern_memory_path_for_state
# ---------------------------------------------------------------------------

def test_pattern_memory_path_from_state_config(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import pattern_memory_path_for_state
    custom = tmp_path / "custom_memory.json"
    state = {"agent_config": {"swarm": {"pattern_memory_path": str(custom)}}}
    result = pattern_memory_path_for_state(state)
    assert result == custom


def test_pattern_memory_path_from_env(monkeypatch, tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import pattern_memory_path_for_state
    custom = tmp_path / "env_memory.json"
    monkeypatch.setenv("SWARM_PATTERN_MEMORY_PATH", str(custom))
    result = pattern_memory_path_for_state({})
    assert result == custom


def test_pattern_memory_path_default(monkeypatch, tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import pattern_memory_path_for_state
    monkeypatch.delenv("SWARM_PATTERN_MEMORY_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    result = pattern_memory_path_for_state({})
    assert result.name == "pattern_memory.json"
    assert ".swarm" in str(result)


# ---------------------------------------------------------------------------
# _load_store
# ---------------------------------------------------------------------------

def test_load_store_nonexistent(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import _load_store
    path = tmp_path / "nonexistent.json"
    result = _load_store(path)
    assert result == {"version": 2, "namespaces": {}, "vectors": {}}


def test_load_store_invalid_json(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import _load_store
    path = tmp_path / "bad.json"
    path.write_text("not valid json")
    result = _load_store(path)
    assert result == {"version": 2, "namespaces": {}, "vectors": {}}


def test_load_store_non_dict_json(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import _load_store
    import json
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2, 3]))
    result = _load_store(path)
    assert result == {"version": 2, "namespaces": {}, "vectors": {}}


def test_load_store_namespaces_not_dict(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import _load_store
    import json
    path = tmp_path / "no_ns.json"
    path.write_text(json.dumps({"version": 1, "namespaces": "wrong"}))
    result = _load_store(path)
    assert result["namespaces"] == {}
    # v1 stores without a vectors block get one promoted in-memory.
    assert result["vectors"] == {}


def test_load_store_v1_legacy_format_preserved(tmp_path):
    """v1 stores written by older code must still be readable."""
    from backend.App.integrations.infrastructure.pattern_memory import _load_store
    import json
    path = tmp_path / "v1.json"
    path.write_text(json.dumps({"version": 1, "namespaces": {"default": {"k": "v"}}}))
    result = _load_store(path)
    assert result["namespaces"]["default"]["k"] == "v"
    assert result["vectors"] == {}


# ---------------------------------------------------------------------------
# store_pattern — merge behavior
# ---------------------------------------------------------------------------

def test_store_pattern_merge_appends(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "key1", "first value", merge=False)
    store_pattern(path, "default", "key1", "second value", merge=True)
    import json
    data = json.loads(path.read_text())
    assert "first value" in data["namespaces"]["default"]["key1"]
    assert "second value" in data["namespaces"]["default"]["key1"]


def test_store_pattern_no_merge_replaces(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "key1", "old value", merge=False)
    store_pattern(path, "default", "key1", "new value", merge=False)
    import json
    data = json.loads(path.read_text())
    assert data["namespaces"]["default"]["key1"] == "new value"


def test_store_pattern_empty_key_raises(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    with pytest.raises(ValueError):
        store_pattern(path, "default", "", "value")


def test_store_pattern_empty_value_raises(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    with pytest.raises(ValueError):
        store_pattern(path, "default", "key", "   ")


# ---------------------------------------------------------------------------
# store_consolidated_pattern
# ---------------------------------------------------------------------------

def test_store_consolidated_pattern(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_consolidated_pattern
    path = tmp_path / "patterns.json"
    store_consolidated_pattern("dream:pm", "Reusable pattern text", ["task-1", "task-2"], path=path)
    import json
    data = json.loads(path.read_text())
    ns = data["namespaces"]["consolidated"]
    assert "dream:pm" in ns
    assert "provenance" in ns["dream:pm"]
    assert "task-1" in ns["dream:pm"]


def test_store_consolidated_pattern_empty_key_raises(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_consolidated_pattern
    path = tmp_path / "patterns.json"
    with pytest.raises(ValueError):
        store_consolidated_pattern("", "value", [], path=path)


def test_store_consolidated_pattern_empty_value_raises(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_consolidated_pattern
    path = tmp_path / "patterns.json"
    with pytest.raises(ValueError):
        store_consolidated_pattern("key", "   ", [], path=path)


def test_store_consolidated_pattern_default_path(monkeypatch, tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import store_consolidated_pattern
    monkeypatch.chdir(tmp_path)
    store_consolidated_pattern("dream:test", "Pattern text here", ["t1"])
    import json
    path = tmp_path / ".swarm" / "pattern_memory.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    assert "dream:test" in data["namespaces"]["consolidated"]


# ---------------------------------------------------------------------------
# search_patterns — edge cases
# ---------------------------------------------------------------------------

def test_search_patterns_no_query_tokens(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import search_patterns, store_pattern
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "auth-key", "auth value here", merge=False)
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(path)}}}
    # Very short query that produces no tokens
    hits = search_patterns(state, "ab", limit=5)
    assert isinstance(hits, list)


def test_search_patterns_empty_bucket(tmp_path):
    from backend.App.integrations.infrastructure.pattern_memory import search_patterns
    path = tmp_path / "empty_mem.json"
    import json
    path.write_text(json.dumps({"version": 1, "namespaces": {}}))
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(path)}}}
    hits = search_patterns(state, "anything", limit=5)
    assert hits == []


def test_format_pattern_memory_block_no_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY", "1")
    from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
    state = {}
    # No patterns stored, so no hits
    block = format_pattern_memory_block(state, "no matches here")
    assert block == ""


# ---------------------------------------------------------------------------
# Semantic layer — uses a stub embedding provider for determinism.
# Real-model smoke tests live in tests/smoke/test_pattern_memory_smoke.py.
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Hand-crafted provider — emits a 4-dim vector based on keyword hits."""

    name = "stub"
    dim = 4

    _AXES = ("auth", "deploy", "design", "memory")

    def embed(self, texts):
        out = []
        for text in texts:
            lowered = text.lower()
            vec = [1.0 if axis in lowered else 0.0 for axis in self._AXES]
            norm = sum(v * v for v in vec) ** 0.5
            if norm:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


@pytest.fixture
def _semantic_provider(monkeypatch):
    from backend.App.integrations.infrastructure import embedding_service
    embedding_service.reset_embedding_provider()
    stub = _StubEmbedder()
    monkeypatch.setattr(embedding_service, "_provider_singleton", stub)
    monkeypatch.delenv("SWARM_PATTERN_MEMORY_SEMANTIC", raising=False)
    monkeypatch.delenv("SWARM_PATTERN_MEMORY_SEMANTIC_WEIGHT", raising=False)
    yield stub
    embedding_service.reset_embedding_provider()


def test_store_pattern_writes_vector_when_provider_available(tmp_path, _semantic_provider):
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "auth-jwt", "JWT auth body", merge=False)
    import json
    raw = json.loads(path.read_text())
    assert raw["version"] == 2
    assert "auth-jwt" in raw["vectors"]["default"]
    assert len(raw["vectors"]["default"]["auth-jwt"]) == _StubEmbedder.dim


def test_store_pattern_skips_vector_when_provider_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_PATTERN_MEMORY_SEMANTIC", "0")
    from backend.App.integrations.infrastructure import embedding_service
    embedding_service.reset_embedding_provider()
    from backend.App.integrations.infrastructure.pattern_memory import store_pattern
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "k", "v", merge=False)
    import json
    raw = json.loads(path.read_text())
    assert raw.get("vectors", {}).get("default", {}) == {}


def test_search_prefers_semantic_match_over_token_overlap(tmp_path, _semantic_provider):
    """Semantic provider should rank token-disjoint but semantically close
    entries above noisy token matches."""
    from backend.App.integrations.infrastructure.pattern_memory import (
        search_patterns,
        store_pattern,
    )
    path = tmp_path / "mem.json"
    # Both entries share zero meaningful tokens with the query, but only
    # the first one shares a semantic axis ("auth").
    store_pattern(path, "default", "credential-flow", "auth handshake notes", merge=False)
    store_pattern(path, "default", "deploy-pipeline", "deploy CI rollout", merge=False)
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(path)}}}
    hits = search_patterns(state, "auth login flow", limit=2)
    assert hits, "semantic provider should produce hits"
    assert hits[0][0] == "credential-flow"


def test_search_falls_back_to_token_when_vector_dim_mismatches(tmp_path, _semantic_provider):
    """Stored vectors with the wrong dim must be ignored, not crash."""
    from backend.App.integrations.infrastructure.pattern_memory import (
        search_patterns,
        store_pattern,
    )
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "auth-jwt", "JWT auth body", merge=False)
    # Corrupt the stored vector to simulate a model-change.
    import json
    raw = json.loads(path.read_text())
    raw["vectors"]["default"]["auth-jwt"] = [0.1] * 999
    path.write_text(json.dumps(raw))
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(path)}}}
    hits = search_patterns(state, "JWT", limit=3)
    # Token overlap on "JWT" still triggers — we get a hit even though the
    # cosine path was skipped due to dim mismatch.
    assert hits and hits[0][0] == "auth-jwt"


def test_search_works_when_provider_is_null(tmp_path, monkeypatch):
    """Token-overlap path must still function with embeddings disabled."""
    monkeypatch.setenv("SWARM_PATTERN_MEMORY_SEMANTIC", "0")
    from backend.App.integrations.infrastructure import embedding_service
    embedding_service.reset_embedding_provider()
    from backend.App.integrations.infrastructure.pattern_memory import (
        search_patterns,
        store_pattern,
    )
    path = tmp_path / "mem.json"
    store_pattern(path, "default", "auth-jwt", "JWT keys", merge=False)
    state = {"agent_config": {"swarm": {"pattern_memory": True, "pattern_memory_path": str(path)}}}
    hits = search_patterns(state, "jwt", limit=3)
    assert hits and hits[0][0] == "auth-jwt"
