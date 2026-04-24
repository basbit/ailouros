"""Unit tests for backend.App.integrations.infrastructure.mcp.memory_tools."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.mcp.memory_tools import (
    handle_memory_tool_call,
    memory_tools_available,
    memory_tools_definitions,
)


# ---------------------------------------------------------------------------
# memory_tools_available
# ---------------------------------------------------------------------------


def test_memory_tools_available_empty_workspace_root():
    os.environ.pop("SWARM_MEMORY_TOOLS", None)
    assert memory_tools_available("") is False


def test_memory_tools_available_flag_off(tmp_path):
    os.environ["SWARM_MEMORY_TOOLS"] = "0"
    try:
        assert memory_tools_available(str(tmp_path)) is False
    finally:
        os.environ.pop("SWARM_MEMORY_TOOLS", None)


def test_memory_tools_available_flag_on_valid_dir(tmp_path):
    os.environ["SWARM_MEMORY_TOOLS"] = "1"
    try:
        assert memory_tools_available(str(tmp_path)) is True
    finally:
        os.environ.pop("SWARM_MEMORY_TOOLS", None)


def test_memory_tools_available_flag_on_missing_dir():
    os.environ["SWARM_MEMORY_TOOLS"] = "1"
    try:
        assert memory_tools_available("/does/not/exist/xyz123") is False
    finally:
        os.environ.pop("SWARM_MEMORY_TOOLS", None)


def test_memory_tools_available_whitespace_root():
    os.environ.pop("SWARM_MEMORY_TOOLS", None)
    assert memory_tools_available("   ") is False


# ---------------------------------------------------------------------------
# memory_tools_definitions
# ---------------------------------------------------------------------------


def test_memory_tools_definitions_returns_four_tools():
    defs = memory_tools_definitions()
    assert len(defs) == 4


def test_memory_tools_definitions_tool_names():
    defs = memory_tools_definitions()
    names = {d["function"]["name"] for d in defs}
    assert names == {"search_memory", "store_pattern", "store_episode", "get_past_failures"}


def test_memory_tools_definitions_all_have_type_function():
    for d in memory_tools_definitions():
        assert d["type"] == "function"


def test_memory_tools_definitions_search_memory_has_query_required():
    defs = memory_tools_definitions()
    search_def = next(d for d in defs if d["function"]["name"] == "search_memory")
    assert "query" in search_def["function"]["parameters"]["required"]


def test_memory_tools_definitions_store_pattern_required_fields():
    defs = memory_tools_definitions()
    store_def = next(d for d in defs if d["function"]["name"] == "store_pattern")
    required = store_def["function"]["parameters"]["required"]
    assert "key" in required
    assert "value" in required


def test_memory_tools_definitions_store_episode_required_fields():
    defs = memory_tools_definitions()
    ep_def = next(d for d in defs if d["function"]["name"] == "store_episode")
    assert "body" in ep_def["function"]["parameters"]["required"]


def test_memory_tools_definitions_get_past_failures_no_required():
    defs = memory_tools_definitions()
    fail_def = next(d for d in defs if d["function"]["name"] == "get_past_failures")
    # No required fields — all optional
    assert fail_def["function"]["parameters"]["required"] == []


# ---------------------------------------------------------------------------
# handle_memory_tool_call — unknown tool raises ValueError
# ---------------------------------------------------------------------------


def test_handle_memory_tool_call_unknown_tool_raises():
    with pytest.raises(ValueError, match="Unknown memory tool"):
        handle_memory_tool_call("nonexistent_tool", {}, {})


def test_handle_memory_tool_call_unknown_tool_message_contains_name():
    try:
        handle_memory_tool_call("bad_tool", {}, {})
    except ValueError as exc:
        assert "bad_tool" in str(exc)


# ---------------------------------------------------------------------------
# handle_memory_tool_call — search_memory
# ---------------------------------------------------------------------------


def test_handle_search_memory_empty_query():
    result = handle_memory_tool_call("search_memory", {"query": ""}, {})
    data = json.loads(result)
    assert "error" in data


def test_handle_search_memory_returns_results():
    mock_hit = MagicMock()
    mock_hit.source = "pattern"
    mock_hit.label = "my-key"
    mock_hit.body = "some pattern body"
    mock_hit.score = 3.5

    with patch(
        "backend.App.integrations.infrastructure.unified_memory.search_memory",
        return_value=[mock_hit],
    ):
        result = handle_memory_tool_call("search_memory", {"query": "auth pattern"}, {})
    data = json.loads(result)
    assert data["count"] == 1
    assert data["results"][0]["source"] == "pattern"
    assert data["results"][0]["label"] == "my-key"


def test_handle_search_memory_limit_clamped():
    with patch(
        "backend.App.integrations.infrastructure.unified_memory.search_memory",
        return_value=[],
    ) as mock_search:
        handle_memory_tool_call("search_memory", {"query": "foo", "limit": 999}, {})
    _, kwargs = mock_search.call_args
    assert kwargs["limit"] <= 20


# ---------------------------------------------------------------------------
# handle_memory_tool_call — store_pattern
# ---------------------------------------------------------------------------


def test_handle_store_pattern_missing_key():
    result = handle_memory_tool_call("store_pattern", {"value": "val"}, {})
    data = json.loads(result)
    assert "error" in data


def test_handle_store_pattern_missing_value():
    result = handle_memory_tool_call("store_pattern", {"key": "k"}, {})
    data = json.loads(result)
    assert "error" in data


def test_handle_store_pattern_success(tmp_path):
    with patch(
        "backend.App.integrations.infrastructure.pattern_memory.pattern_memory_path_for_state",
        return_value=tmp_path / "pattern_memory.json",
    ), patch(
        "backend.App.integrations.infrastructure.pattern_memory.store_pattern"
    ) as mock_store:
        result = handle_memory_tool_call(
            "store_pattern",
            {"key": "auth:token", "value": "Use JWT with refresh", "namespace": "patterns"},
            {},
        )
    data = json.loads(result)
    assert data["stored"] is True
    assert data["key"] == "auth:token"
    assert data["namespace"] == "patterns"
    mock_store.assert_called_once()


def test_handle_store_pattern_default_namespace(tmp_path):
    with patch(
        "backend.App.integrations.infrastructure.pattern_memory.pattern_memory_path_for_state",
        return_value=tmp_path / "pm.json",
    ), patch(
        "backend.App.integrations.infrastructure.pattern_memory.store_pattern"
    ):
        result = handle_memory_tool_call(
            "store_pattern",
            {"key": "k", "value": "v"},
            {},
        )
    data = json.loads(result)
    assert data["namespace"] == "default"


# ---------------------------------------------------------------------------
# handle_memory_tool_call — store_episode
# ---------------------------------------------------------------------------


def test_handle_store_episode_empty_body():
    result = handle_memory_tool_call("store_episode", {"body": ""}, {})
    data = json.loads(result)
    assert "error" in data


def test_handle_store_episode_success():
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory.append_episode"
    ) as mock_append:
        result = handle_memory_tool_call(
            "store_episode",
            {"body": "some episode text", "step_id": "pm", "namespace": "myns"},
            {},
        )
    data = json.loads(result)
    assert data["stored"] is True
    assert data["step_id"] == "pm"
    assert data["namespace"] == "myns"
    mock_append.assert_called_once()


def test_handle_store_episode_default_step_id():
    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory.append_episode"
    ) as mock_append:
        handle_memory_tool_call("store_episode", {"body": "text"}, {})
    _, kwargs = mock_append.call_args
    assert kwargs["step_id"] == "mcp"


def test_handle_store_episode_injects_namespace_into_state():
    captured_states: list[dict] = []

    def capture(state, *, step_id, body, **_):
        captured_states.append(state)

    with patch(
        "backend.App.integrations.infrastructure.cross_task_memory.append_episode",
        side_effect=capture,
    ):
        handle_memory_tool_call(
            "store_episode",
            {"body": "some text", "namespace": "custom-ns"},
            {},
        )
    state = captured_states[0]
    ctm_cfg = state["agent_config"]["swarm"]["cross_task_memory"]
    assert ctm_cfg["namespace"] == "custom-ns"
    assert ctm_cfg["enabled"] is True


# ---------------------------------------------------------------------------
# handle_memory_tool_call — get_past_failures
# ---------------------------------------------------------------------------


def test_handle_get_past_failures_returns_list():
    with patch(
        "backend.App.integrations.infrastructure.failure_memory.get_warnings_for",
        return_value=[],
    ):
        result = handle_memory_tool_call("get_past_failures", {}, {})
    data = json.loads(result)
    assert "failures" in data
    assert data["count"] == 0


def test_handle_get_past_failures_with_results():
    fake_failures = [
        {
            "step": "dev",
            "summary": "missing file deliverables",
            "count": 2,
            "last_seen": 1713000000.0,
            "score": 3.0,
        }
    ]
    with patch(
        "backend.App.integrations.infrastructure.failure_memory.get_warnings_for",
        return_value=fake_failures,
    ):
        result = handle_memory_tool_call(
            "get_past_failures",
            {"query": "missing file", "limit": 5},
            {},
        )
    data = json.loads(result)
    assert data["count"] >= 1
    assert data["failures"][0]["step"] == "dev"


def test_handle_get_past_failures_limit_clamped():
    with patch(
        "backend.App.integrations.infrastructure.failure_memory.get_warnings_for",
        return_value=[],
    ) as mock_get:
        handle_memory_tool_call("get_past_failures", {"limit": 999}, {})
    _, kwargs = mock_get.call_args
    assert kwargs["limit"] <= 20
