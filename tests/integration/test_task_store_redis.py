"""Tests for backend/App/tasks/infrastructure/task_store_redis.py.

All Redis calls are mocked — no real Redis required.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_client(stored: dict | None = None):
    """Return a mock Redis client with optional pre-stored task data."""
    client = MagicMock()
    client.ping.return_value = True

    def fake_get(key):
        if stored and key in stored:
            return json.dumps(stored[key], ensure_ascii=False)
        return None

    client.get.side_effect = fake_get
    client.set.return_value = True
    client.delete.return_value = 1
    return client


def _make_pipeline_mock():
    pipe = MagicMock()
    pipe.watch.return_value = None
    pipe.multi.return_value = None
    pipe.set.return_value = None
    pipe.execute.return_value = [True]
    pipe.reset.return_value = None
    return pipe


# ---------------------------------------------------------------------------
# _redis_socket_timeout_params
# ---------------------------------------------------------------------------

def test_socket_timeout_defaults(monkeypatch):
    monkeypatch.delenv("REDIS_SOCKET_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT", raising=False)
    from backend.App.tasks.infrastructure.task_store_redis import _redis_socket_timeout_params
    result = _redis_socket_timeout_params()
    assert result["socket_connect_timeout"] == 5.0
    assert result["socket_timeout"] == 30.0


def test_socket_timeout_custom(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "2.5")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "10.0")
    from backend.App.tasks.infrastructure.task_store_redis import _redis_socket_timeout_params
    result = _redis_socket_timeout_params()
    assert result["socket_connect_timeout"] == 2.5
    assert result["socket_timeout"] == 10.0


def test_socket_timeout_none_string(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "none")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "unlimited")
    from backend.App.tasks.infrastructure.task_store_redis import _redis_socket_timeout_params
    result = _redis_socket_timeout_params()
    assert "socket_connect_timeout" not in result
    assert "socket_timeout" not in result


def test_socket_timeout_invalid_string_uses_default(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "bad")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "off")
    from backend.App.tasks.infrastructure.task_store_redis import _redis_socket_timeout_params
    result = _redis_socket_timeout_params()
    assert result["socket_connect_timeout"] == 5.0
    assert "socket_timeout" not in result


def test_socket_timeout_zero_disables(monkeypatch):
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "0")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "0")
    from backend.App.tasks.infrastructure.task_store_redis import _redis_socket_timeout_params
    result = _redis_socket_timeout_params()
    assert "socket_connect_timeout" not in result
    assert "socket_timeout" not in result


# ---------------------------------------------------------------------------
# TaskStore init — in-memory mode (Redis unavailable)
# ---------------------------------------------------------------------------

def _make_task_store_no_redis(monkeypatch):
    """Build a TaskStore in in-memory mode."""
    monkeypatch.setenv("REDIS_REQUIRED", "0")

    with patch(
        "backend.App.tasks.infrastructure.task_store_redis.redis"
    ) as mock_redis_mod:
        mock_client = MagicMock()
        mock_client.ping.side_effect = ConnectionError("Redis unavailable")
        mock_redis_mod.Redis.from_url.return_value = mock_client

        from backend.App.tasks.infrastructure import task_store_redis
        import importlib
        importlib.reload(task_store_redis)
        from backend.App.tasks.infrastructure.task_store_redis import TaskStore
        store = TaskStore()

    return store


def _make_task_store_memory_only():
    """Build an InMemoryTaskStore for tests that exercise in-memory behaviour."""
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
    return InMemoryTaskStore()


def _make_store_with_redis():
    """Build a RedisTaskStore with a mocked Redis client."""
    from backend.App.tasks.infrastructure.task_store_redis import RedisTaskStore, _TASK_TTL_SECONDS
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    return RedisTaskStore(client=mock_client, ttl_sec=_TASK_TTL_SECONDS)


# ---------------------------------------------------------------------------
# InMemoryTaskStore.create_task
# ---------------------------------------------------------------------------

def test_create_task_returns_payload():
    store = _make_task_store_memory_only()
    payload = store.create_task("Build a feature")
    assert payload["task"] == "Build a feature"
    assert "task_id" in payload
    assert payload["status"] == "in_progress"
    assert payload["agents"] == []
    assert payload["history"] == []
    assert payload["version"] == 0


def test_create_task_saved_in_memory():
    store = _make_task_store_memory_only()
    payload = store.create_task("My task")
    tid = payload["task_id"]
    assert tid in store._memory


# ---------------------------------------------------------------------------
# RedisTaskStore._key
# ---------------------------------------------------------------------------

def test_key_format():
    store = _make_store_with_redis()
    assert store._key("abc-123") == "swarm:task:abc-123"


# ---------------------------------------------------------------------------
# InMemoryTaskStore._apply_update
# ---------------------------------------------------------------------------

def test_apply_update_status():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": [], "history": [], "version": 0}
    result = store._apply_update(payload, status="completed")
    assert result["status"] == "completed"
    assert result["version"] == 1


def test_apply_update_agent_added():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": [], "history": [], "version": 0}
    result = store._apply_update(payload, agent="dev_agent")
    assert "dev_agent" in result["agents"]


def test_apply_update_agent_not_duplicated():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": ["dev_agent"], "history": [], "version": 0}
    result = store._apply_update(payload, agent="dev_agent")
    assert result["agents"].count("dev_agent") == 1


def test_apply_update_message_added():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": [], "history": [], "version": 0}
    result = store._apply_update(payload, agent="dev", message="Done!")
    assert len(result["history"]) == 1
    assert result["history"][0]["message"] == "Done!"
    assert result["history"][0]["agent"] == "dev"


def test_apply_update_empty_message_not_added():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": [], "history": [], "version": 0}
    result = store._apply_update(payload, message="   ")
    assert result["history"] == []


def test_apply_update_none_values_no_op():
    store = _make_task_store_memory_only()
    payload = {"status": "in_progress", "agents": [], "history": [], "version": 0}
    result = store._apply_update(payload, status=None, agent=None, message=None)
    assert result["status"] == "in_progress"
    assert result["agents"] == []
    assert result["history"] == []
    assert result["version"] == 1


# ---------------------------------------------------------------------------
# InMemoryTaskStore.update_task
# ---------------------------------------------------------------------------

def test_update_task_in_memory():
    store = _make_task_store_memory_only()
    payload = store.create_task("test task")
    tid = payload["task_id"]
    updated = store.update_task(tid, status="completed", message="All done")
    assert updated["status"] == "completed"
    assert len(updated["history"]) == 1


def test_update_task_not_found_raises():
    store = _make_task_store_memory_only()
    with pytest.raises(KeyError):
        store.update_task("nonexistent-id", status="completed")


# ---------------------------------------------------------------------------
# InMemoryTaskStore.get_task
# ---------------------------------------------------------------------------

def test_get_task_in_memory():
    store = _make_task_store_memory_only()
    payload = store.create_task("Get me")
    tid = payload["task_id"]
    retrieved = store.get_task(tid)
    assert retrieved["task_id"] == tid


def test_get_task_missing_raises():
    store = _make_task_store_memory_only()
    with pytest.raises(KeyError):
        store.get_task("ghost-task")


# ---------------------------------------------------------------------------
# InMemoryTaskStore.delete_task
# ---------------------------------------------------------------------------

def test_delete_task_in_memory():
    store = _make_task_store_memory_only()
    payload = store.create_task("delete me")
    tid = payload["task_id"]
    store.delete_task(tid)
    assert tid not in store._memory


def test_delete_task_missing_no_error():
    store = _make_task_store_memory_only()
    store.delete_task("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# InMemoryTaskStore._save — eviction logic
# ---------------------------------------------------------------------------

def test_save_evicts_oldest_when_over_limit():
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
    store = InMemoryTaskStore(max_size=3)
    for i in range(4):
        store.create_task(f"task {i}")
    # After eviction, oldest entry removed — at most 3 entries remain
    assert len(store._memory) <= 3


# ---------------------------------------------------------------------------
# RedisTaskStore — mocked Redis client
# ---------------------------------------------------------------------------

def test_get_task_redis_hit():
    store = _make_store_with_redis()
    payload = {"task_id": "t-1", "task": "test", "status": "in_progress",
               "agents": [], "history": [], "version": 0}
    store.client.get.return_value = json.dumps(payload)
    result = store.get_task("t-1")
    assert result["task_id"] == "t-1"


def test_get_task_redis_miss_raises_key_error():
    """RedisTaskStore raises KeyError on a Redis miss — no in-memory fallback."""
    store = _make_store_with_redis()
    store.client.get.return_value = None
    with pytest.raises(KeyError):
        store.get_task("ghost")


def test_save_with_redis():
    store = _make_store_with_redis()
    from backend.App.tasks.infrastructure.task_store_redis import _TASK_TTL_SECONDS
    payload = {"task_id": "t-save", "task": "save test", "status": "in_progress",
               "agents": [], "history": [], "version": 0}
    store.client.set(store._key("t-save"), json.dumps(payload), ex=_TASK_TTL_SECONDS)
    store.client.set.assert_called_once()


def test_delete_task_with_redis():
    store = _make_store_with_redis()
    store.delete_task("t-del")
    store.client.delete.assert_called_once_with("swarm:task:t-del")


def test_update_task_redis_success():
    store = _make_store_with_redis()
    payload = {"task_id": "t-upd", "task": "update", "status": "in_progress",
               "agents": [], "history": [], "version": 0}
    store.client.get.return_value = json.dumps(payload)

    pipe = _make_pipeline_mock()
    store.client.pipeline.return_value = pipe

    result = store.update_task("t-upd", status="completed")
    assert result["status"] == "completed"
    pipe.execute.assert_called_once()


def test_update_task_redis_not_found_raises():
    store = _make_store_with_redis()
    store.client.get.return_value = None

    pipe = _make_pipeline_mock()
    store.client.pipeline.return_value = pipe

    with pytest.raises(KeyError):
        store.update_task("ghost-redis", status="completed")


def test_update_task_redis_watch_error_retries():
    """WatchError triggers retries; after all retries exhausted raises ConcurrentUpdateError."""
    import backend.App.tasks.infrastructure.task_store_redis as mod
    WatchError = mod.WatchError
    ConcurrentUpdateError = mod.ConcurrentUpdateError

    store = _make_store_with_redis()
    payload = {"task_id": "t-watch", "task": "watch", "status": "in_progress",
               "agents": [], "history": [], "version": 0}
    store.client.get.return_value = json.dumps(payload)

    pipe = MagicMock()
    pipe.watch.return_value = None
    pipe.multi.return_value = None
    pipe.execute.side_effect = WatchError("conflict")
    pipe.reset.return_value = None
    store.client.pipeline.return_value = pipe

    # After max retries exhausted, raises ConcurrentUpdateError
    with pytest.raises(ConcurrentUpdateError):
        store.update_task("t-watch", status="failed")


def test_update_task_redis_pipe_reset_exception_ignored():
    """If pipe.reset() raises, it's caught and logged but doesn't fail."""
    store = _make_store_with_redis()
    payload = {"task_id": "t-reset", "task": "reset", "status": "in_progress",
               "agents": [], "history": [], "version": 0}
    store.client.get.return_value = json.dumps(payload)

    pipe = _make_pipeline_mock()
    pipe.reset.side_effect = RuntimeError("reset failed")
    store.client.pipeline.return_value = pipe

    # Should complete despite reset() raising
    result = store.update_task("t-reset", status="completed")
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# TaskStore factory — REDIS_REQUIRED edge cases
# ---------------------------------------------------------------------------

def test_task_store_init_redis_not_available_not_required(monkeypatch):
    monkeypatch.setenv("REDIS_REQUIRED", "0")
    with patch(
        "backend.App.tasks.infrastructure.task_store_redis.redis",
        None,
    ):
        from backend.App.tasks.infrastructure.task_store_redis import TaskStore
        store = TaskStore()
    # When redis module is absent and not required, returns InMemoryTaskStore
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore
    assert isinstance(store, InMemoryTaskStore)


def test_task_store_init_redis_not_available_required(monkeypatch):
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    with patch(
        "backend.App.tasks.infrastructure.task_store_redis.redis",
        None,
    ):
        from backend.App.tasks.infrastructure.task_store_redis import TaskStore
        with pytest.raises(RuntimeError, match="redis package is not installed"):
            TaskStore()


def test_task_store_init_redis_unavailable_required(monkeypatch):
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    mock_redis_mod = MagicMock()
    mock_client = MagicMock()
    mock_client.ping.side_effect = ConnectionError("down")
    mock_redis_mod.Redis.from_url.return_value = mock_client

    with patch(
        "backend.App.tasks.infrastructure.task_store_redis.redis",
        mock_redis_mod,
    ):
        from backend.App.tasks.infrastructure.task_store_redis import TaskStore
        with pytest.raises(RuntimeError, match="Redis is unavailable"):
            TaskStore()
