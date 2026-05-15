from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from backend.App.integrations.infrastructure.qdrant_client import (
    VectorHit,
    get_vector_store,
)
from backend.App.shared.infrastructure.env_flags import is_truthy_env


def shared_history_enabled() -> bool:
    return is_truthy_env("SWARM_SHARED_HISTORY_ENABLED", default=False)


def shared_history_collection() -> str:
    raw = (os.getenv("SWARM_SHARED_HISTORY_COLLECTION") or "").strip()
    return raw or "shared_conversation"


def shared_history_retention_days() -> int:
    raw = (os.getenv("SWARM_SHARED_HISTORY_RETENTION_DAYS") or "").strip()
    if not raw:
        return 30
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"SWARM_SHARED_HISTORY_RETENTION_DAYS must be an integer, got {raw!r}"
        ) from exc
    return max(1, value)


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    task_id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredMessage:
    message: ConversationMessage
    score: float


def _serialise(message: ConversationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "task_id": message.task_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "metadata": dict(message.metadata),
    }


def _parse(payload: dict[str, Any]) -> ConversationMessage:
    created_raw = payload.get("created_at")
    if isinstance(created_raw, datetime):
        created = created_raw
    elif isinstance(created_raw, str) and created_raw:
        created = datetime.fromisoformat(created_raw)
    else:
        created = datetime.now(tz=timezone.utc)
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return ConversationMessage(
        id=str(payload.get("id") or ""),
        task_id=str(payload.get("task_id") or ""),
        role=str(payload.get("role") or ""),
        content=str(payload.get("content") or ""),
        created_at=created,
        metadata=metadata,
    )


def _embed_text(message: ConversationMessage) -> str:
    return f"{message.role}: {message.content}".strip()


class ConversationStore:
    def __init__(
        self,
        *,
        vector_store: Optional[Any] = None,
        collection: Optional[str] = None,
        embedding_provider: Optional[Any] = None,
    ) -> None:
        self._vector_store = vector_store if vector_store is not None else get_vector_store()
        self._collection = (collection or shared_history_collection()).strip() or "shared_conversation"
        self._embedding_provider = embedding_provider

    @staticmethod
    def make_id() -> str:
        return f"msg-{uuid.uuid4().hex}"

    @property
    def collection(self) -> str:
        return self._collection

    def _provider(self) -> Optional[Any]:
        if self._embedding_provider is not None:
            return self._embedding_provider
        try:
            from backend.App.integrations.infrastructure.embedding_service import (
                get_embedding_provider,
            )
        except ImportError:
            return None
        provider = get_embedding_provider()
        if getattr(provider, "name", "") == "null":
            return None
        return provider

    def _embed(self, text: str) -> list[float]:
        if not text.strip():
            return []
        provider = self._provider()
        if provider is None:
            return []
        vectors = provider.embed([text[:4000]])
        if not vectors:
            return []
        return [float(x) for x in vectors[0]]

    def purge_expired(self, *, now: Optional[datetime] = None) -> int:
        from datetime import timedelta

        days = shared_history_retention_days()
        moment = now if now is not None else datetime.now(tz=timezone.utc)
        cutoff = moment - timedelta(days=days)
        scroll_limit = 5000
        seen_total = 0
        ids_to_delete: list[str] = []
        while True:
            page = self._vector_store.scroll(self._collection, limit=scroll_limit)
            if not page:
                break
            seen_total += len(page)
            for hit in page:
                payload = hit.payload if isinstance(hit, VectorHit) else getattr(hit, "payload", None)
                if not isinstance(payload, dict):
                    continue
                created_raw = payload.get("created_at")
                if not isinstance(created_raw, str) or not created_raw:
                    continue
                try:
                    created = datetime.fromisoformat(created_raw)
                except ValueError:
                    continue
                if created < cutoff:
                    point_id = getattr(hit, "id", "")
                    if isinstance(point_id, str) and point_id:
                        ids_to_delete.append(point_id)
            if len(page) < scroll_limit:
                break
            if seen_total >= scroll_limit * 4:
                break
        if ids_to_delete:
            self._vector_store.delete(self._collection, ids_to_delete)
        return len(ids_to_delete)

    def append(self, message: ConversationMessage) -> None:
        if not message.id or not message.task_id:
            raise ValueError("ConversationMessage requires id and task_id")
        vector = self._embed(_embed_text(message))
        self._vector_store.upsert(
            self._collection, message.id, vector, _serialise(message)
        )

    def recent(self, task_id: str, *, limit: int = 50) -> list[ConversationMessage]:
        if not task_id:
            return []
        limit = max(1, min(500, limit))
        hits = self._vector_store.scroll(self._collection, limit=limit * 4)
        messages: list[ConversationMessage] = []
        for hit in hits:
            payload = hit.payload if isinstance(hit, VectorHit) else getattr(hit, "payload", None)
            if not isinstance(payload, dict):
                continue
            if payload.get("task_id") != task_id:
                continue
            try:
                messages.append(_parse(payload))
            except (ValueError, TypeError):
                continue
        messages.sort(key=lambda m: m.created_at)
        return messages[-limit:]

    def search(
        self, query: str, *, task_id: Optional[str] = None, k: int = 10
    ) -> list[ScoredMessage]:
        if not query.strip():
            return []
        k = max(1, min(100, k))
        vector = self._embed(query)
        if vector:
            raw_hits = self._vector_store.search(self._collection, vector, limit=k * 4)
        else:
            raw_hits = self._vector_store.scroll(self._collection, limit=k * 4)
        results: list[ScoredMessage] = []
        query_lower = query.lower()
        for hit in raw_hits:
            payload = hit.payload if isinstance(hit, VectorHit) else getattr(hit, "payload", None)
            if not isinstance(payload, dict):
                continue
            if task_id and payload.get("task_id") != task_id:
                continue
            try:
                msg = _parse(payload)
            except (ValueError, TypeError):
                continue
            score = float(getattr(hit, "score", 0.0))
            if not vector and query_lower in msg.content.lower():
                score = max(score, 1.0)
            results.append(ScoredMessage(message=msg, score=score))
            if len(results) >= k:
                break
        return results


__all__ = [
    "ConversationMessage",
    "ConversationStore",
    "ScoredMessage",
    "shared_history_collection",
    "shared_history_enabled",
    "shared_history_retention_days",
]
