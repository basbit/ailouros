from __future__ import annotations

import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_state(workspace_root: str = "") -> dict[str, Any]:
    return {
        "workspace_root": workspace_root,
        "agent_config": {
            "swarm": {
                "cross_task_memory": {
                    "enabled": True,
                    "namespace": "test_ns",
                }
            }
        },
    }


# ---------------------------------------------------------------------------
# pattern_memory — signing on write
# ---------------------------------------------------------------------------

def test_store_pattern_writes_provenance(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        _load_store,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(
        mem_path,
        "ns1",
        "my-key",
        "my-value",
        agent="dev",
        spec_id="spec-abc",
        spec_hash="hash-001",
    )
    data = _load_store(mem_path)
    prov = data["provenance"]["ns1"]["my-key"]
    assert prov["agent"] == "dev"
    assert prov["spec_id"] == "spec-abc"
    assert prov["spec_hash"] == "hash-001"
    assert isinstance(prov["recorded_at"], float)


def test_store_pattern_provenance_no_fields_is_empty_strings(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        _load_store,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(mem_path, "ns1", "key2", "val2")
    data = _load_store(mem_path)
    prov = data["provenance"]["ns1"]["key2"]
    assert prov["agent"] == ""
    assert prov["spec_id"] == ""
    assert prov["spec_hash"] == ""


def test_store_pattern_provenance_timestamp_is_recent(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        _load_store,
    )

    before = time.time()
    mem_path = tmp_path / "pm.json"
    store_pattern(mem_path, "ns", "k", "v", spec_id="s", spec_hash="h")
    after = time.time()
    data = _load_store(mem_path)
    ts = data["provenance"]["ns"]["k"]["recorded_at"]
    assert before <= ts <= after


# ---------------------------------------------------------------------------
# pattern_memory — quarantine filter on read
# ---------------------------------------------------------------------------

def test_search_patterns_filters_stale_spec_hash(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        search_patterns,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(
        mem_path, "default", "stale-key", "stale value from old spec",
        spec_id="spec-x", spec_hash="old-hash",
    )
    state = {
        "agent_config": {
            "swarm": {
                "pattern_memory_path": str(mem_path),
                "pattern_memory": True,
            }
        }
    }
    results = search_patterns(
        state, "stale value",
        current_spec_id="spec-x",
        current_spec_hash="new-hash",
    )
    keys = [r[0] for r in results]
    assert "stale-key" not in keys


def test_search_patterns_allows_matching_spec_hash(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        search_patterns,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(
        mem_path, "default", "current-key", "current value",
        spec_id="spec-y", spec_hash="current-hash",
    )
    state = {
        "agent_config": {
            "swarm": {
                "pattern_memory_path": str(mem_path),
                "pattern_memory": True,
            }
        }
    }
    results = search_patterns(
        state, "current value",
        current_spec_id="spec-y",
        current_spec_hash="current-hash",
    )
    keys = [r[0] for r in results]
    assert "current-key" in keys


def test_search_patterns_allows_different_spec_id(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        search_patterns,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(
        mem_path, "default", "other-key", "other spec value",
        spec_id="spec-other", spec_hash="some-hash",
    )
    state = {
        "agent_config": {
            "swarm": {
                "pattern_memory_path": str(mem_path),
                "pattern_memory": True,
            }
        }
    }
    results = search_patterns(
        state, "other spec value",
        current_spec_id="spec-z",
        current_spec_hash="different-hash",
    )
    keys = [r[0] for r in results]
    assert "other-key" in keys


# ---------------------------------------------------------------------------
# pattern_memory — list_quarantined_patterns
# ---------------------------------------------------------------------------

def test_list_quarantined_patterns_returns_stale(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        store_pattern,
        list_quarantined_patterns,
    )

    mem_path = tmp_path / "pm.json"
    store_pattern(mem_path, "ns", "q-key", "q-value", spec_id="S1", spec_hash="H1")
    store_pattern(mem_path, "ns", "ok-key", "ok-value", spec_id="S1", spec_hash="H2")

    quarantined = list_quarantined_patterns(
        mem_path, current_spec_id="S1", current_spec_hash="H2"
    )
    keys = [e["key"] for e in quarantined]
    assert "q-key" in keys
    assert "ok-key" not in keys


def test_list_quarantined_patterns_empty_when_no_spec_id(tmp_path: Path) -> None:
    from backend.App.integrations.infrastructure.pattern_memory import (
        list_quarantined_patterns,
    )

    mem_path = tmp_path / "pm.json"
    result = list_quarantined_patterns(mem_path, current_spec_id="", current_spec_hash="h")
    assert result == []


# ---------------------------------------------------------------------------
# cross_task_memory — provenance on write and quarantine on read
# ---------------------------------------------------------------------------

def test_append_episode_stores_provenance() -> None:
    from unittest.mock import patch

    from backend.App.integrations.infrastructure.cross_task_memory import (
        _LOCAL_EPISODES,
        append_episode,
        memory_namespace,
    )

    _LOCAL_EPISODES.clear()
    state = _dummy_state()

    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=None,
    ):
        append_episode(
            state,
            step_id="pm",
            body="some output text",
            task_id="t1",
            agent="pm_agent",
            spec_id="spec-1",
            spec_hash="h-abc",
        )
    ns = memory_namespace(state)
    episodes = _LOCAL_EPISODES.get(ns, [])
    assert episodes
    prov = episodes[0].get("_provenance")
    assert prov is not None
    assert prov["agent"] == "pm_agent"
    assert prov["spec_id"] == "spec-1"
    assert prov["spec_hash"] == "h-abc"
    _LOCAL_EPISODES.clear()


def test_list_quarantined_episodes_returns_stale() -> None:
    from unittest.mock import patch

    from backend.App.integrations.infrastructure.cross_task_memory import (
        _LOCAL_EPISODES,
        append_episode,
        list_quarantined_episodes,
    )

    _LOCAL_EPISODES.clear()
    state = _dummy_state()
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=None,
    ):
        append_episode(
            state, step_id="ba", body="old output", task_id="t2",
            spec_id="spec-Q", spec_hash="hash-old",
        )
        append_episode(
            state, step_id="arch", body="fresh output", task_id="t2",
            spec_id="spec-Q", spec_hash="hash-new",
        )

    quarantined = list_quarantined_episodes(
        state, current_spec_id="spec-Q", current_spec_hash="hash-new"
    )
    bodies = [e["body"] for e in quarantined]
    assert any("old output" in b for b in bodies)
    assert not any("fresh output" in b for b in bodies)
    _LOCAL_EPISODES.clear()


def test_search_episodes_excludes_quarantined() -> None:
    from unittest.mock import patch

    from backend.App.integrations.infrastructure.cross_task_memory import (
        _LOCAL_EPISODES,
        append_episode,
        search_episodes,
    )

    _LOCAL_EPISODES.clear()
    state = _dummy_state()
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=None,
    ):
        append_episode(
            state, step_id="pm", body="quarantined content keyword", task_id="t3",
            spec_id="spec-R", spec_hash="old-R",
        )
        append_episode(
            state, step_id="pm", body="fresh content keyword", task_id="t3",
            spec_id="spec-R", spec_hash="new-R",
        )

    results = search_episodes(
        state, "keyword",
        current_spec_id="spec-R",
        current_spec_hash="new-R",
    )
    bodies = [ep["body"] for ep, _ in results]
    assert not any("quarantined" in b for b in bodies)
    assert any("fresh" in b for b in bodies)
    _LOCAL_EPISODES.clear()
