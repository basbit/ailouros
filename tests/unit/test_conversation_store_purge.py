from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.App.integrations.infrastructure.conversation_store import (
    ConversationMessage,
    ConversationStore,
)
from backend.App.integrations.infrastructure.qdrant_client import InMemoryVectorStore


@pytest.fixture(autouse=True)
def _retention(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_RETENTION_DAYS", "7")
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")


def _message(created_at: datetime, content: str) -> ConversationMessage:
    return ConversationMessage(
        id=ConversationStore.make_id(),
        task_id="task-1",
        role="user",
        content=content,
        created_at=created_at,
    )


def test_purge_expired_deletes_old_entries():
    backend = InMemoryVectorStore()
    store = ConversationStore(vector_store=backend, collection="conv_purge")
    now = datetime.now(tz=timezone.utc)
    store.append(_message(now - timedelta(days=30), "old"))
    store.append(_message(now - timedelta(days=2), "recent"))
    removed = store.purge_expired(now=now)
    assert removed == 1
    remaining = [m.content for m in store.recent("task-1")]
    assert remaining == ["recent"]


def test_purge_expired_keeps_everything_when_all_recent():
    backend = InMemoryVectorStore()
    store = ConversationStore(vector_store=backend, collection="conv_purge")
    now = datetime.now(tz=timezone.utc)
    store.append(_message(now - timedelta(days=1), "a"))
    store.append(_message(now - timedelta(days=3), "b"))
    assert store.purge_expired(now=now) == 0


def test_purge_expired_returns_zero_on_empty_store():
    backend = InMemoryVectorStore()
    store = ConversationStore(vector_store=backend, collection="conv_purge")
    assert store.purge_expired() == 0
