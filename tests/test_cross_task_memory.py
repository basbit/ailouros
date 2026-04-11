"""Tests for backend/App/integrations/infrastructure/cross_task_memory.py."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.cross_task_memory import (
    _LOCAL_EPISODES,
    _build_episode_payload,
    _list_key,
    _max_items,
    _mem_cfg,
    _normalize_token,
    _parse_structured_memory_body,
    _render_structured_memory,
    _score_episode,
    _swarm_block,
    _truthy,
    append_episode,
    cross_task_memory_enabled,
    format_cross_task_memory_block,
    memory_artifact_state_key,
    memory_namespace,
    normalize_memory_artifact,
    search_episodes,
    should_inject_at_step,
)


def _state(**kwargs):
    return dict(kwargs)


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    (True, True),
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    (False, False),
    ("0", False),
    ("false", False),
    (None, False),
    (1, False),
    ("", False),
])
def test_truthy(val, expected):
    assert _truthy(val) == expected


# ---------------------------------------------------------------------------
# _swarm_block
# ---------------------------------------------------------------------------

def test_swarm_block_extracts_swarm():
    state = _state(agent_config={"swarm": {"key": "val"}})
    assert _swarm_block(state) == {"key": "val"}


def test_swarm_block_missing():
    assert _swarm_block({}) == {}


def test_swarm_block_non_dict_swarm():
    state = _state(agent_config={"swarm": "invalid"})
    assert _swarm_block(state) == {}


# ---------------------------------------------------------------------------
# _mem_cfg
# ---------------------------------------------------------------------------

def test_mem_cfg_returns_config():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"enabled": True}}})
    assert _mem_cfg(state) == {"enabled": True}


def test_mem_cfg_non_dict_returns_empty():
    state = _state(agent_config={"swarm": {"cross_task_memory": "yes"}})
    assert _mem_cfg(state) == {}


# ---------------------------------------------------------------------------
# cross_task_memory_enabled
# ---------------------------------------------------------------------------

def test_cross_task_memory_enabled_env(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    assert cross_task_memory_enabled({}) is True


def test_cross_task_memory_enabled_config():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"enabled": True}}})
    assert cross_task_memory_enabled(state) is True


def test_cross_task_memory_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "0")
    assert cross_task_memory_enabled({}) is False


# ---------------------------------------------------------------------------
# memory_namespace
# ---------------------------------------------------------------------------

def test_memory_namespace_explicit():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"namespace": "myns"}}})
    assert memory_namespace(state) == "myns"


def test_memory_namespace_from_workspace_root():
    state = _state(workspace_root="/home/user/project")
    ns = memory_namespace(state)
    assert ns.startswith("ws:")
    assert len(ns) == len("ws:") + 20


def test_memory_namespace_default():
    assert memory_namespace({}) == "default"


def test_memory_namespace_truncates_long_name():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"namespace": "x" * 200}}})
    assert len(memory_namespace(state)) == 128


# ---------------------------------------------------------------------------
# _list_key
# ---------------------------------------------------------------------------

def test_list_key():
    assert _list_key("myns") == "swarm:xmem:myns"


# ---------------------------------------------------------------------------
# _max_items
# ---------------------------------------------------------------------------

def test_max_items_default():
    assert _max_items({}) == 400


def test_max_items_from_config():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"max_list_items": 50}}})
    assert _max_items(state) == 50


def test_max_items_clamps_low():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"max_list_items": 1}}})
    assert _max_items(state) == 10


def test_max_items_clamps_high():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"max_list_items": 99999}}})
    assert _max_items(state) == 5000


def test_max_items_invalid_string():
    state = _state(agent_config={"swarm": {"cross_task_memory": {"max_list_items": "bad"}}})
    assert _max_items(state) == 400


# ---------------------------------------------------------------------------
# _normalize_token
# ---------------------------------------------------------------------------

def test_normalize_token_basic():
    tokens = _normalize_token("Hello World test")
    assert "hello" in tokens
    assert "world" in tokens
    assert "test" in tokens


def test_normalize_token_short_words_excluded():
    tokens = _normalize_token("hi go to the store")
    assert "hi" not in tokens
    assert "go" not in tokens


def test_normalize_token_punct_split():
    tokens = _normalize_token("foo.bar,baz")
    assert "foo" in tokens
    assert "bar" in tokens
    assert "baz" in tokens


# ---------------------------------------------------------------------------
# _score_episode
# ---------------------------------------------------------------------------

def test_score_episode_exact_match_bonus():
    score = _score_episode("authentication", "we handle authentication carefully")
    assert score > 4.0  # exact substring match adds 4.0


def test_score_episode_token_overlap():
    score = _score_episode("user authentication", "user login authentication system")
    assert score >= 2.0


def test_score_episode_no_overlap():
    score = _score_episode("xyz123", "completely different content here")
    assert score == 0.0


# ---------------------------------------------------------------------------
# append_episode (local store, no redis)
# ---------------------------------------------------------------------------

def test_append_episode_disabled_does_nothing(monkeypatch):
    monkeypatch.delenv("SWARM_CROSS_TASK_MEMORY", raising=False)
    state = _state()
    ns = "test_append_disabled"
    _LOCAL_EPISODES.pop(ns, None)
    append_episode(state, step_id="pm", body="some content", task_id="t1")
    assert ns not in _LOCAL_EPISODES


def test_append_episode_enabled_stores_locally(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    _LOCAL_EPISODES.pop(ns, None)
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        append_episode({}, step_id="pm", body="important content", task_id="tid1")
    episodes = _LOCAL_EPISODES.get(ns, [])
    assert len(episodes) >= 1
    assert episodes[0]["step"] == "pm"
    assert "important content" in episodes[0]["body"]
    assert episodes[0]["task_id"] == "tid1"
    assert episodes[0]["facts"] == ["important content"]
    assert episodes[0]["facts_are_verified"] is False
    assert episodes[0]["structured"] is False


def test_append_episode_empty_body_skipped(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    before_count = len(_LOCAL_EPISODES.get(ns, []))
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        append_episode({}, step_id="pm", body="   ")
    assert len(_LOCAL_EPISODES.get(ns, [])) == before_count


def test_append_episode_respects_max_items(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    # Use a limit above the min of 10 so clamping doesn't interfere
    state = _state(agent_config={"swarm": {"cross_task_memory": {"max_list_items": 12}}})
    ns = "default"
    _LOCAL_EPISODES.pop(ns, None)
    # Patch so _redis() returns None (forces local store path)
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        for i in range(20):
            append_episode(state, step_id="pm", body=f"content {i}")
    assert len(_LOCAL_EPISODES.get(ns, [])) <= 12


def test_append_episode_with_redis(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    mock_redis = MagicMock()
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        return_value=mock_redis,
    ):
        append_episode({}, step_id="pm", body="redis content", task_id="t2")
    mock_redis.lpush.assert_called_once()
    mock_redis.ltrim.assert_called_once()


# ---------------------------------------------------------------------------
# search_episodes
# ---------------------------------------------------------------------------

def test_search_episodes_disabled(monkeypatch):
    monkeypatch.delenv("SWARM_CROSS_TASK_MEMORY", raising=False)
    assert search_episodes({}, "query") == []


def test_search_episodes_returns_relevant(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    _LOCAL_EPISODES[ns] = [
        {"step": "pm", "task_id": "t1", "body": "authentication and user management"},
        {"step": "ba", "task_id": "t2", "body": "database schema for products"},
    ]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        results = search_episodes({}, "authentication", limit=5)
    assert len(results) >= 1
    assert results[0][0]["body"] == "authentication and user management"


def test_search_episodes_empty_store(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    _LOCAL_EPISODES[ns] = []
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        results = search_episodes({}, "any query", limit=5)
    assert results == []


# ---------------------------------------------------------------------------
# should_inject_at_step
# ---------------------------------------------------------------------------

def test_should_inject_at_step_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "0")
    assert should_inject_at_step({}, "pm") is False


def test_should_inject_at_step_default_pm(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    assert should_inject_at_step({}, "pm") is True


def test_should_inject_at_step_default_not_pm(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    assert should_inject_at_step({}, "dev") is False


def test_should_inject_at_step_custom_list(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    state = _state(
        agent_config={"swarm": {"cross_task_memory": {"inject_at_steps": ["pm", "ba"]}}}
    )
    assert should_inject_at_step(state, "ba") is True
    assert should_inject_at_step(state, "dev") is False


# ---------------------------------------------------------------------------
# format_cross_task_memory_block
# ---------------------------------------------------------------------------

def test_format_cross_task_memory_block_wrong_step(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    result = format_cross_task_memory_block({}, "my query", current_step="dev")
    assert result == ""


def test_format_cross_task_memory_block_no_hits(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    _LOCAL_EPISODES[ns] = []
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        result = format_cross_task_memory_block({}, "nohits query", current_step="pm")
    assert result == ""


def test_format_cross_task_memory_block_with_hits(monkeypatch):
    monkeypatch.setenv("SWARM_CROSS_TASK_MEMORY", "1")
    ns = "default"
    _LOCAL_EPISODES[ns] = [
        {"step": "pm", "task_id": "abc123", "body": "user authentication flow with JWT"}
    ]
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory._redis",
        side_effect=lambda: None,
    ):
        result = format_cross_task_memory_block({}, "authentication JWT", current_step="pm")
    assert "authentication" in result or "JWT" in result or "pm" in result


def test_parse_structured_memory_body_from_json_block():
    body = (
        "summary\n```json\n"
        '{"facts":["fact a"],"hypotheses":["hyp"],"decisions":["dec"],"dead_ends":[],"constraints":["c"]}\n'
        "```"
    )
    result = _parse_structured_memory_body(body)
    assert result["structured"] is True
    assert result["facts"] == ["fact a"]
    assert result["hypotheses"] == ["hyp"]
    assert result["decisions"] == ["dec"]
    assert result["constraints"] == ["c"]


def test_build_episode_payload_renders_structured_body():
    episode = _build_episode_payload(
        step_id="pm",
        body='```json\n{"facts":["fact a"],"decisions":["dec"]}\n```',
        task_id="t1",
    )
    assert episode["structured"] is True
    assert episode["facts"] == ["fact a"]
    assert "## Facts" in episode["body"]
    assert "## Decisions" in episode["body"]


def test_normalize_memory_artifact_prefers_verified_facts():
    artifact = normalize_memory_artifact(
        {
            "verified_facts": ["JWT auth exists in app/security.py"],
            "facts": ["fallback factual note"],
            "decisions": ["Split auth and billing subtasks"],
        }
    )

    assert artifact["facts"] == ["JWT auth exists in app/security.py", "fallback factual note"]
    assert artifact["facts_are_verified"] is True


def test_normalize_memory_artifact_filters_generic_decisions():
    artifact = normalize_memory_artifact(
        {
            "decisions": [
                "Implement the remaining scope according to the specification.",
                "Split auth and billing into separate subtasks.",
            ]
        }
    )

    assert artifact["decisions"] == ["Split auth and billing into separate subtasks."]


def test_memory_artifact_state_key_for_custom_role():
    assert memory_artifact_state_key("crole_doc_writer") == "crole_doc_writer_memory_artifact"


def test_render_structured_memory_uses_sections():
    rendered = _render_structured_memory(
        {
            "facts": ["fact a"],
            "hypotheses": [],
            "decisions": ["dec"],
            "dead_ends": [],
            "constraints": ["constraint"],
        }
    )
    assert "## Facts" in rendered
    assert "- fact a" in rendered
    assert "## Decisions" in rendered
    assert "## Constraints" in rendered
