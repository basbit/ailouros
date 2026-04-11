"""Tests for RedisTaskStore optimistic-lock retry logic (update_task)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def _make_redis_store():
    """Build a RedisTaskStore with a mocked Redis client."""
    from backend.App.tasks.infrastructure.task_store_redis import RedisTaskStore, _TASK_TTL_SECONDS
    mock_client = MagicMock()
    return RedisTaskStore(client=mock_client, ttl_sec=_TASK_TTL_SECONDS)


def _make_pipeline(execute_side_effect=None, execute_return=None):
    pipe = MagicMock()
    pipe.watch.return_value = None
    pipe.multi.return_value = None
    pipe.reset.return_value = None
    pipe.set.return_value = None
    if execute_side_effect is not None:
        pipe.execute.side_effect = execute_side_effect
    elif execute_return is not None:
        pipe.execute.return_value = execute_return
    else:
        pipe.execute.return_value = [True]
    return pipe


def test_watch_error_retries_and_succeeds():
    """WatchError on first two attempts; succeeds on the third."""
    try:
        from redis.exceptions import WatchError
    except ImportError:
        pytest.skip("redis package not installed")

    store = _make_redis_store()
    task_payload = {
        "task_id": "retry-test-task",
        "task": "build",
        "status": "in_progress",
        "agents": [],
        "history": [],
        "version": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    store.client.get.return_value = json.dumps(task_payload)

    execute_count = [0]

    def side_effect():
        execute_count[0] += 1
        if execute_count[0] < 3:
            raise WatchError("forced conflict")
        return [True]

    pipe = _make_pipeline(execute_side_effect=side_effect)
    store.client.pipeline.return_value = pipe

    result = store.update_task("retry-test-task", status="completed")

    assert result["status"] == "completed"
    assert execute_count[0] >= 3, "Expected at least 3 execute() calls (2 fails + 1 success)"


def test_watch_error_exhausts_retries_raises():
    """After all WatchError retries are exhausted, raises ConcurrentUpdateError."""
    try:
        from redis.exceptions import WatchError
    except ImportError:
        pytest.skip("redis package not installed")

    from backend.App.tasks.infrastructure.task_store_redis import ConcurrentUpdateError

    store = _make_redis_store()
    task_payload = {
        "task_id": "exhaust-test",
        "task": "build",
        "status": "in_progress",
        "agents": [],
        "history": [],
        "version": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    store.client.get.return_value = json.dumps(task_payload)

    pipe = _make_pipeline(execute_side_effect=WatchError("always conflicts"))
    store.client.pipeline.return_value = pipe

    with pytest.raises(ConcurrentUpdateError):
        store.update_task("exhaust-test", status="done_fallback")


def test_update_task_missing_raises_key_error():
    """update_task raises KeyError when the task does not exist in Redis."""
    store = _make_redis_store()
    store.client.get.return_value = None
    pipe = _make_pipeline()
    store.client.pipeline.return_value = pipe

    with pytest.raises(KeyError):
        store.update_task("nonexistent", status="completed")


def test_update_task_in_memory_store():
    """InMemoryTaskStore.update_task works without Redis."""
    from backend.App.tasks.infrastructure.task_store_memory import InMemoryTaskStore

    store = InMemoryTaskStore()
    store.create_task("build something")
    # create_task returns the payload; retrieve the id from it
    task_id = store.create_task("mem-task")["task_id"]

    result = store.update_task(task_id, status="completed", agent="qa", message="all green")
    assert result["status"] == "completed"
    assert "qa" in result["agents"]
    assert result["history"][0]["message"] == "all green"
    assert result["version"] == 1
