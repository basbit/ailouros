from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.App.integrations.infrastructure.conversation_store import (
    ConversationStore,
    shared_history_enabled,
)

router = APIRouter()

_store: Optional[ConversationStore] = None


def _conversation_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store


def _serialize_message(message: Any) -> dict[str, Any]:
    return {
        "id": message.id,
        "task_id": message.task_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "metadata": dict(message.metadata),
    }


@router.get("/v1/conversation/{task_id}")
def conversation_for_task(
    task_id: str,
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    if not shared_history_enabled():
        raise HTTPException(status_code=404, detail="shared history is disabled")
    messages = _conversation_store().recent(task_id, limit=limit)
    return {
        "task_id": task_id,
        "messages": [_serialize_message(message) for message in messages],
        "shared_history_enabled": True,
    }


__all__ = ["router"]
