from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.conversation_store import (
    ConversationMessage,
    ConversationStore,
)
from backend.App.integrations.infrastructure.qdrant_client import InMemoryVectorStore


@pytest.fixture()
def store_factory(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")

    def _make(vector_store: InMemoryVectorStore) -> ConversationStore:
        return ConversationStore(vector_store=vector_store, collection="conv_test")

    return _make


def _make_message(task_id: str, content: str) -> ConversationMessage:
    from datetime import datetime, timezone

    return ConversationMessage(
        id=ConversationStore.make_id(),
        task_id=task_id,
        role="user",
        content=content,
        created_at=datetime.now(tz=timezone.utc),
    )


def test_recent_after_simulated_restart_uses_vector_store(store_factory):
    shared_vector_store = InMemoryVectorStore()
    first_instance = store_factory(shared_vector_store)
    first_instance.append(_make_message("task-1", "hello"))
    first_instance.append(_make_message("task-1", "world"))
    rebuilt_instance = store_factory(shared_vector_store)
    messages = rebuilt_instance.recent("task-1")
    contents = [message.content for message in messages]
    assert contents == ["hello", "world"]


def test_recent_isolates_by_task_id(store_factory):
    shared_vector_store = InMemoryVectorStore()
    instance = store_factory(shared_vector_store)
    instance.append(_make_message("task-1", "a"))
    instance.append(_make_message("task-2", "b"))
    only_task_1 = [m.content for m in instance.recent("task-1")]
    only_task_2 = [m.content for m in instance.recent("task-2")]
    assert only_task_1 == ["a"]
    assert only_task_2 == ["b"]


def test_recent_respects_limit(store_factory):
    shared_vector_store = InMemoryVectorStore()
    instance = store_factory(shared_vector_store)
    for index in range(5):
        instance.append(_make_message("task-1", f"line-{index}"))
    last_two = [m.content for m in instance.recent("task-1", limit=2)]
    assert last_two == ["line-3", "line-4"]
