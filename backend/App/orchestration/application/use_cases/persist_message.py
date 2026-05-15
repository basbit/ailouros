from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.App.integrations.infrastructure.conversation_store import (
    ConversationMessage,
    ConversationStore,
    shared_history_enabled,
)
from backend.App.orchestration.application.privacy.conversation_policy import (
    Policy,
    apply_policy,
)

_default_store: Optional[ConversationStore] = None


def _store() -> ConversationStore:
    global _default_store
    if _default_store is None:
        _default_store = ConversationStore()
    return _default_store


def reset_default_store() -> None:
    global _default_store
    _default_store = None


def persist_message(
    *,
    task_id: str,
    role: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    policy: Optional[Policy] = None,
    store: Optional[ConversationStore] = None,
) -> Optional[str]:
    if not shared_history_enabled():
        return None
    if not task_id or not isinstance(content, str):
        return None
    sanitised = content.strip()
    if not sanitised:
        return None
    target = store if store is not None else _store()
    message = ConversationMessage(
        id=ConversationStore.make_id(),
        task_id=task_id,
        role=role,
        content=sanitised,
        created_at=datetime.now(timezone.utc),
        metadata=metadata or {},
    )
    effective_policy = policy if policy is not None else Policy()
    transformed = apply_policy(message, effective_policy)
    if transformed is None:
        return None
    target.append(transformed)
    return transformed.id


__all__ = ["persist_message", "reset_default_store"]
