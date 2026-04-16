"""Real-model smoke tests for the semantic layer of ``pattern_memory``.

Verifies that with a real sentence-transformers provider, the hybrid
ranking surfaces semantically related entries even when there is no
token overlap with the query — the case the legacy token-only scorer
would miss completely.
"""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401 — imported so conftest sees the smoke marker registration

from backend.App.integrations.infrastructure.pattern_memory import (
    search_patterns,
    store_pattern,
)


def _state_for(path: Path) -> dict:
    return {
        "agent_config": {
            "swarm": {
                "pattern_memory": True,
                "pattern_memory_path": str(path),
            }
        }
    }


def test_real_provider_writes_vector_alongside_pattern(real_embedding_provider, tmp_path):
    path = tmp_path / "patterns.json"
    store_pattern(path, "default", "auth-jwt", "Use JWT with refresh tokens", merge=False)
    import json
    raw = json.loads(path.read_text())
    vec = raw["vectors"]["default"]["auth-jwt"]
    assert isinstance(vec, list)
    assert len(vec) >= 64, "real model should produce a dense vector"
    assert any(abs(v) > 1e-6 for v in vec)


def test_semantic_match_beats_token_disjoint_competitor(real_embedding_provider, tmp_path):
    """Query about user authentication has zero token overlap with our
    entries but is semantically very close to the 'auth-jwt' entry."""
    path = tmp_path / "patterns.json"
    store_pattern(path, "default", "auth-jwt",
                  "Issue JSON web tokens and refresh credentials; rotate signing keys weekly.",
                  merge=False)
    store_pattern(path, "default", "ci-cd",
                  "Build pipeline pushes Docker image to a container registry.",
                  merge=False)
    store_pattern(path, "default", "design-system",
                  "Colour palette, typography scale and spacing system for the app shell.",
                  merge=False)

    hits = search_patterns(
        _state_for(path),
        query="how do users sign in with passwords or OAuth providers",
        limit=3,
    )
    assert hits, "real provider should produce at least one hit"
    assert hits[0][0] == "auth-jwt", (
        f"expected auth-jwt to win semantically, got {[(k, s) for k, _, s in hits]}"
    )


def test_paraphrase_query_matches_stored_entry(real_embedding_provider, tmp_path):
    """A paraphrased query without any shared word still ranks the right entry."""
    path = tmp_path / "patterns.json"
    store_pattern(
        path,
        "default",
        "redis-cache-strategy",
        "Cache aside pattern with TTL eviction for hot keys.",
        merge=False,
    )
    store_pattern(
        path,
        "default",
        "kubernetes-deploy",
        "Helm chart with rolling updates and HPA.",
        merge=False,
    )

    hits = search_patterns(
        _state_for(path),
        query="how do we keep frequently accessed data in memory for low latency",
        limit=2,
    )
    assert hits
    assert hits[0][0] == "redis-cache-strategy"


def test_semantic_score_strictly_higher_than_token_only(real_embedding_provider, tmp_path, monkeypatch):
    """The semantic layer does real work: for a token-disjoint paraphrase,
    semantic mode produces a higher score for the right entry than the
    token-only fallback does for any entry."""
    from backend.App.integrations.infrastructure import embedding_service

    path = tmp_path / "patterns.json"
    # Write entries with vectors (semantic ON during write).
    monkeypatch.delenv("SWARM_PATTERN_MEMORY_SEMANTIC", raising=False)
    embedding_service.reset_embedding_provider()
    store_pattern(path, "default", "redis-cache-strategy",
                  "Cache aside pattern with TTL eviction for hot keys.", merge=False)
    store_pattern(path, "default", "kubernetes-deploy",
                  "Helm chart with rolling updates and HPA.", merge=False)

    query = "how do we keep frequently accessed data in memory for low latency"

    # Semantic ON: real provider, hybrid score.
    embedding_service.reset_embedding_provider()
    monkeypatch.setenv("SWARM_EMBEDDING_PROVIDER", "local")
    hits_semantic = search_patterns(_state_for(path), query=query, limit=2)
    assert hits_semantic and hits_semantic[0][0] == "redis-cache-strategy"
    semantic_top_score = hits_semantic[0][2]

    # Semantic OFF: only token overlap.
    monkeypatch.setenv("SWARM_PATTERN_MEMORY_SEMANTIC", "0")
    embedding_service.reset_embedding_provider()
    hits_tokens = search_patterns(_state_for(path), query=query, limit=2)
    token_top_score = hits_tokens[0][2] if hits_tokens else 0.0

    assert semantic_top_score > token_top_score, (
        f"semantic={semantic_top_score} should outrank token={token_top_score}"
    )
