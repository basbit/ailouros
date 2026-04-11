"""Unit tests for BackgroundAgent (K-10).

All tests are isolation-only — no real threads, file watchers, or LLM calls.
"""
from __future__ import annotations

import os
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 1. Default disabled
# ---------------------------------------------------------------------------

def test_default_disabled() -> None:
    """SWARM_BACKGROUND_AGENT is not set in the environment by default."""
    value = os.getenv("SWARM_BACKGROUND_AGENT", "0")
    assert value != "1", (
        "Expected SWARM_BACKGROUND_AGENT to be absent or '0', got: %r" % value
    )
    assert (value == "1") is False


# ---------------------------------------------------------------------------
# 2. start() is a no-op when disabled
# ---------------------------------------------------------------------------

def test_start_when_disabled_is_noop() -> None:
    """start() must not spawn a thread when _AGENT_ENABLED is False."""
    import backend.App.orchestration.application.background_agent as mod

    with patch.object(mod, "_AGENT_ENABLED", False):
        from backend.App.orchestration.application.background_agent import BackgroundAgent

        agent = BackgroundAgent(watch_paths=["/tmp"])
        agent.start()

        assert agent._worker_thread is None
        assert agent._running is False


# ---------------------------------------------------------------------------
# 3. drain_recommendations on fresh agent returns []
# ---------------------------------------------------------------------------

def test_drain_empty() -> None:
    """A freshly constructed agent has no queued recommendations."""
    from backend.App.orchestration.application.background_agent import BackgroundAgent

    agent = BackgroundAgent(watch_paths=["/tmp"])
    result = agent.drain_recommendations()

    assert result == []


# ---------------------------------------------------------------------------
# 4. pending_count on fresh agent returns 0
# ---------------------------------------------------------------------------

def test_pending_count_empty() -> None:
    """pending_count() returns 0 when the recommendation queue is empty."""
    from backend.App.orchestration.application.background_agent import BackgroundAgent

    agent = BackgroundAgent(watch_paths=["/tmp"])

    assert agent.pending_count() == 0


# ---------------------------------------------------------------------------
# 5. _call_llm falls back gracefully on ImportError
# ---------------------------------------------------------------------------

def test_call_llm_fallback_on_import_error() -> None:
    """_call_llm returns a safe static dict (severity='info') when the
    AnthropicClient import fails — it must never raise."""
    import backend.App.orchestration.application.background_agent as mod

    # Simulate the import inside _call_llm failing.
    with patch.dict("sys.modules", {
        "backend.App.integrations.infrastructure.llm.client": None,
    }):
        result = mod._call_llm("modified", "/some/file.py")

    assert isinstance(result, dict)
    assert result.get("severity") == "info"
    assert "message" in result
    assert "suggested_action" in result


# ---------------------------------------------------------------------------
# 6. _build_prompt contains event_type and path
# ---------------------------------------------------------------------------

def test_build_prompt_contains_path() -> None:
    """_build_prompt must embed both the event type and the file path."""
    from backend.App.orchestration.application.background_agent import _build_prompt

    prompt = _build_prompt("modified", "/foo/bar.py")

    assert "modified" in prompt
    assert "/foo/bar.py" in prompt


# ---------------------------------------------------------------------------
# 7. _default_watch_paths returns [] when env var is empty
# ---------------------------------------------------------------------------

def test_default_watch_paths_empty_env() -> None:
    """_default_watch_paths() returns [] when the env variable is blank."""
    import backend.App.orchestration.application.background_agent as mod

    with patch.object(mod, "_WATCH_PATHS_ENV", ""):
        result = mod._default_watch_paths()

    assert result == []


# ---------------------------------------------------------------------------
# 8. _default_watch_paths with multiple paths
# ---------------------------------------------------------------------------

def test_default_watch_paths_multiple() -> None:
    import backend.App.orchestration.application.background_agent as mod

    with patch.object(mod, "_WATCH_PATHS_ENV", "/src, /lib, /tests"):
        result = mod._default_watch_paths()

    assert result == ["/src", "/lib", "/tests"]


def test_default_watch_paths_single() -> None:
    import backend.App.orchestration.application.background_agent as mod

    with patch.object(mod, "_WATCH_PATHS_ENV", "/src/project"):
        result = mod._default_watch_paths()

    assert result == ["/src/project"]


def test_default_watch_paths_filters_empty_segments() -> None:
    import backend.App.orchestration.application.background_agent as mod

    with patch.object(mod, "_WATCH_PATHS_ENV", "/src,,/lib"):
        result = mod._default_watch_paths()

    assert "" not in result
    assert len(result) == 2


# ---------------------------------------------------------------------------
# 9. Recommendation dataclass
# ---------------------------------------------------------------------------

def test_recommendation_fields() -> None:
    from backend.App.orchestration.application.background_agent import Recommendation

    rec = Recommendation(
        event_type="modified",
        path="/src/main.py",
        message="File changed",
        severity="info",
        suggested_action="Review the diff",
    )
    assert rec.event_type == "modified"
    assert rec.path == "/src/main.py"
    assert rec.severity == "info"
    assert isinstance(rec.timestamp, float)


def test_recommendation_custom_timestamp() -> None:
    from backend.App.orchestration.application.background_agent import Recommendation

    rec = Recommendation(
        event_type="created",
        path="/tmp/new.py",
        message="New file",
        severity="warning",
        suggested_action="Add tests",
        timestamp=12345.0,
    )
    assert rec.timestamp == 12345.0


# ---------------------------------------------------------------------------
# 10. _call_llm — success path with mock client
# ---------------------------------------------------------------------------

def test_call_llm_success_parses_json() -> None:
    import json
    import backend.App.orchestration.application.background_agent as mod

    response = json.dumps({
        "message": "File was modified",
        "severity": "warning",
        "suggested_action": "Run tests",
    })
    mock_client = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_client.complete.return_value = response

    with patch(
        "backend.App.integrations.infrastructure.llm.client.AnthropicClient",
        return_value=mock_client,
        create=True,
    ):
        result = mod._call_llm("modified", "/src/main.py")

    assert result["message"] == "File was modified"
    assert result["severity"] == "warning"


def test_call_llm_json_in_markdown_fences() -> None:
    import json
    import backend.App.orchestration.application.background_agent as mod

    inner = json.dumps({
        "message": "Check types",
        "severity": "warning",
        "suggested_action": "Run mypy",
    })
    response = f"```json\n{inner}\n```"
    mock_client = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_client.complete.return_value = response

    with patch(
        "backend.App.integrations.infrastructure.llm.client.AnthropicClient",
        return_value=mock_client,
        create=True,
    ):
        result = mod._call_llm("modified", "/src/types.py")

    assert result["severity"] == "warning"


def test_call_llm_invalid_json_fallback() -> None:
    import backend.App.orchestration.application.background_agent as mod

    mock_client = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_client.complete.return_value = "not valid json"

    with patch(
        "backend.App.integrations.infrastructure.llm.client.AnthropicClient",
        return_value=mock_client,
        create=True,
    ):
        result = mod._call_llm("created", "/src/new.py")

    assert "message" in result
    assert result["severity"] == "info"


# ---------------------------------------------------------------------------
# 11. BackgroundAgent — _on_file_event enqueues
# ---------------------------------------------------------------------------

def test_on_file_event_enqueues() -> None:
    import queue
    from backend.App.orchestration.application.background_agent import BackgroundAgent
    from unittest.mock import MagicMock

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._event_queue = queue.Queue()
    event = MagicMock()
    event.event_type = "modified"
    event.path = "/src/x.py"
    agent._on_file_event(event)
    assert not agent._event_queue.empty()


# ---------------------------------------------------------------------------
# 12. BackgroundAgent — drain_recommendations returns items
# ---------------------------------------------------------------------------

def test_drain_recommendations_returns_items() -> None:
    import queue
    from backend.App.orchestration.application.background_agent import BackgroundAgent, Recommendation

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._queue = queue.Queue()
    rec = Recommendation("modified", "/a.py", "msg", "info", "act")
    agent._queue.put(rec)
    result = agent.drain_recommendations()
    assert len(result) == 1
    assert agent._queue.empty()


# ---------------------------------------------------------------------------
# 13. BackgroundAgent — _process_events None sentinel stops loop
# ---------------------------------------------------------------------------

def test_process_events_none_stops_loop() -> None:
    import queue
    import threading
    from backend.App.orchestration.application.background_agent import BackgroundAgent

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._event_queue = queue.Queue()
    agent._queue = queue.Queue()
    agent._running = True

    agent._event_queue.put(None)

    thread = threading.Thread(target=agent._process_events, daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_process_events_produces_recommendation() -> None:
    import queue
    import time
    import threading
    from backend.App.orchestration.application.background_agent import BackgroundAgent
    from unittest.mock import MagicMock

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._event_queue = queue.Queue()
    agent._queue = queue.Queue()
    agent._running = True

    event = MagicMock()
    event.event_type = "modified"
    event.path = "/src/main.py"
    event.timestamp = time.time()

    llm_result = {
        "message": "File changed",
        "severity": "info",
        "suggested_action": "Review",
    }

    agent._event_queue.put(event)
    agent._event_queue.put(None)

    with patch(
        "backend.App.orchestration.application.background_agent._call_llm",
        return_value=llm_result,
    ):
        thread = threading.Thread(target=agent._process_events, daemon=True)
        thread.start()
        thread.join(timeout=3.0)

    recs = agent.drain_recommendations()
    assert len(recs) == 1
    assert recs[0].message == "File changed"


def test_process_events_exception_does_not_crash() -> None:
    import queue
    import threading
    from backend.App.orchestration.application.background_agent import BackgroundAgent
    from unittest.mock import MagicMock

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._event_queue = queue.Queue()
    agent._queue = queue.Queue()
    agent._running = True

    event = MagicMock()
    event.event_type = "error"
    event.path = "/bad.py"

    agent._event_queue.put(event)
    agent._event_queue.put(None)

    with patch(
        "backend.App.orchestration.application.background_agent._call_llm",
        side_effect=RuntimeError("LLM exploded"),
    ):
        thread = threading.Thread(target=agent._process_events, daemon=True)
        thread.start()
        thread.join(timeout=2.0)

    assert agent.drain_recommendations() == []


# ---------------------------------------------------------------------------
# 14. BackgroundAgent — stop
# ---------------------------------------------------------------------------

def test_stop_sets_running_false() -> None:
    import queue
    from backend.App.orchestration.application.background_agent import BackgroundAgent

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._running = True
    agent._watcher = None
    agent._event_queue = queue.Queue()
    agent._worker_thread = None
    agent.stop()
    assert not agent._running


def test_stop_calls_watcher_stop() -> None:
    import queue
    from backend.App.orchestration.application.background_agent import BackgroundAgent
    from unittest.mock import MagicMock

    agent = BackgroundAgent.__new__(BackgroundAgent)
    agent._running = True
    agent._event_queue = queue.Queue()
    agent._worker_thread = None
    mock_watcher = MagicMock()
    agent._watcher = mock_watcher
    agent.stop()
    mock_watcher.stop.assert_called_once()
    assert agent._watcher is None
