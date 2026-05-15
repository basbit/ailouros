from __future__ import annotations

import pytest

from backend.App.integrations.infrastructure.conversation_store import ConversationStore
from backend.App.integrations.infrastructure.qdrant_client import InMemoryVectorStore
from backend.App.orchestration.application.privacy.conversation_policy import Policy
from backend.App.orchestration.application.use_cases.persist_message import (
    persist_message,
)


def _store():
    return ConversationStore(
        vector_store=InMemoryVectorStore(),
        collection="test_persist",
    )


def test_persist_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_SHARED_HISTORY_ENABLED", raising=False)
    result = persist_message(
        task_id="t1", role="user", content="hi", store=_store(),
    )
    assert result is None


def test_persist_writes_when_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")
    store = _store()
    result = persist_message(
        task_id="t1", role="user", content="hello world", store=store,
    )
    assert result is not None
    messages = store.recent("t1")
    assert len(messages) == 1
    assert messages[0].content == "hello world"


def test_persist_strips_empty(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")
    store = _store()
    assert persist_message(task_id="t1", role="user", content="   ", store=store) is None
    assert persist_message(task_id="t1", role="user", content="", store=store) is None


def test_persist_blocks_pii_when_policy_excludes(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")
    store = _store()
    result = persist_message(
        task_id="t1",
        role="user",
        content="email me at john@example.com",
        policy=Policy(exclude_personal=True),
        store=store,
    )
    assert result is None
    assert store.recent("t1") == []


def test_persist_ignores_blank_task_id(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")
    store = _store()
    assert persist_message(task_id="", role="user", content="x", store=store) is None


class _BrokenStore:
    """Test double — every append raises so we can prove persist_message
    surfaces the error instead of silently swallowing it.

    Per docs/review-rules.md §2 (no silent fallbacks), a failing
    conversation backend must propagate, never return None as if the
    write succeeded-but-was-skipped.
    """

    def append(self, _message) -> None:
        raise RuntimeError("backend unavailable")


def test_persist_propagates_backend_failure(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_HISTORY_ENABLED", "1")
    with pytest.raises(RuntimeError, match="backend unavailable"):
        persist_message(
            task_id="t1",
            role="user",
            content="hello",
            store=_BrokenStore(),  # type: ignore[arg-type]
        )
