from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.integrations.infrastructure.cross_task_memory import (
    _LOCAL_EPISODES,
    _build_episode_payload,
    append_episode,
    search_episodes,
)


@pytest.fixture(autouse=True)
def _enable_layer_and_clear_state(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    monkeypatch.delenv("SWARM_CROSS_TASK_MEMORY_SEMANTIC", raising=False)
    monkeypatch.delenv("SWARM_CROSS_TASK_MEMORY_SEMANTIC_WEIGHT", raising=False)
    _LOCAL_EPISODES.clear()
    yield
    _LOCAL_EPISODES.clear()


def test_episode_carries_real_dense_embedding(real_embedding_provider):
    payload = _build_episode_payload(
        step_id="pm",
        body="Project should support OAuth login and refresh-token rotation.",
        task_id="t-1",
    )
    assert "embedding" in payload
    assert len(payload["embedding"]) >= 64
    assert any(abs(value) > 1e-6 for value in payload["embedding"])


def test_paraphrased_query_finds_correct_episode(real_embedding_provider):
    state = {}
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        append_episode(
            state,
            step_id="pm",
            body="Build sign-in flow with passwords and OAuth providers.",
            task_id="login-task",
        )
        append_episode(
            state,
            step_id="pm",
            body="Set up Helm chart with rolling updates for backend service.",
            task_id="deploy-task",
        )
        append_episode(
            state,
            step_id="pm",
            body="Choose a colour palette for the marketing website.",
            task_id="design-task",
        )
        results = search_episodes(state, "how do users authenticate", limit=3)

    assert results, "real provider should find at least one episode"
    top = results[0][0]
    assert top["task_id"] == "login-task", (
        f"expected login-task to win, got {[(r[0]['task_id'], r[1]) for r in results]}"
    )


def test_semantic_finds_episode_token_search_completely_misses(
    real_embedding_provider, monkeypatch
):
    from backend.App.integrations.infrastructure import embedding_service

    state = {}
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        append_episode(
            state,
            step_id="pm",
            body="Reduce roundtrip latency by serving hot keys from Redis.",
            task_id="redis",
        )
        append_episode(
            state,
            step_id="pm",
            body="Distribute upgrades gradually using a Helm rollout strategy.",
            task_id="k8s",
        )

        query = "how to accelerate frequently requested lookups"

        monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY_SEMANTIC", "0")
        embedding_service.reset_embedding_provider()
        token_results = search_episodes(state, query, limit=2)

        monkeypatch.delenv("SWARM_CROSS_TASK_MEMORY_SEMANTIC", raising=False)
        embedding_service.reset_embedding_provider()
        semantic_results = search_episodes(state, query, limit=2)

    assert token_results == [], (
        f"token-only path expected to miss this paraphrase, got {token_results!r}"
    )
    assert semantic_results, "semantic path must find the redis episode"
    assert semantic_results[0][0]["task_id"] == "redis", (
        f"semantic should rank redis first, got "
        f"{[(r[0]['task_id'], r[1]) for r in semantic_results]}"
    )
