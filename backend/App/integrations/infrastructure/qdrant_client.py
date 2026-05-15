from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from backend.App.shared.domain.vector_math import cosine_dense as _cosine
from backend.App.shared.infrastructure.activity_recorder import record as record_activity


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


@dataclass
class _Point:
    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._collections: dict[str, dict[str, _Point]] = {}

    def list_collections(self) -> list[str]:
        with self._lock:
            return list(self._collections.keys())

    def upsert(
        self,
        collection: str,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        if not collection:
            raise ValueError("collection name required")
        if not point_id:
            raise ValueError("point id required")
        with self._lock:
            bucket = self._collections.setdefault(collection, {})
            bucket[point_id] = _Point(id=point_id, vector=list(vector), payload=dict(payload))
        record_activity(
            "qdrant_ops",
            {
                "op": "upsert",
                "backend": "memory",
                "collection": collection,
                "point_id": point_id,
                "vector_dim": len(vector),
            },
        )

    def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[VectorHit]:
        if limit <= 0:
            return []
        with self._lock:
            bucket = self._collections.get(collection) or {}
            points = list(bucket.values())
        if not query_vector:
            hits = [VectorHit(id=p.id, score=0.0, payload=dict(p.payload)) for p in points[:limit]]
        else:
            scored = [
                VectorHit(id=p.id, score=_cosine(query_vector, p.vector), payload=dict(p.payload))
                for p in points
            ]
            scored.sort(key=lambda h: -h.score)
            hits = scored[:limit]
        record_activity(
            "qdrant_ops",
            {
                "op": "search",
                "backend": "memory",
                "collection": collection,
                "vector_dim": len(query_vector),
                "limit": limit,
                "hit_count": len(hits),
            },
        )
        return hits

    def scroll(self, collection: str, limit: int = 100) -> list[VectorHit]:
        if limit <= 0:
            return []
        with self._lock:
            bucket = self._collections.get(collection) or {}
            points = list(bucket.values())
        return [VectorHit(id=p.id, score=0.0, payload=dict(p.payload)) for p in points[:limit]]

    def delete(self, collection: str, point_ids: Iterable[str]) -> None:
        with self._lock:
            bucket = self._collections.get(collection)
            if not bucket:
                return
            for pid in point_ids:
                bucket.pop(pid, None)

    def count(self, collection: str) -> int:
        with self._lock:
            bucket = self._collections.get(collection) or {}
            return len(bucket)


_store_lock = threading.Lock()
_store_instance: Optional[Any] = None


def _qdrant_url() -> str:
    return (os.getenv("SWARM_QDRANT_URL") or "").strip()


_QDRANT_SHARD_PANIC_PATTERN = "Failed to (de)serialize from/to json"


def _is_qdrant_shard_panic(message: str) -> bool:
    return _QDRANT_SHARD_PANIC_PATTERN in (message or "")


def _build_store() -> Any:
    url = _qdrant_url()
    if not url:
        return InMemoryVectorStore()
    try:
        from qdrant_client import QdrantClient  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "SWARM_QDRANT_URL is set but the 'qdrant-client' package is not installed. "
            "Install it (pip install qdrant-client) or unset SWARM_QDRANT_URL to use the "
            "in-memory store."
        ) from exc
    api_key = (os.getenv("SWARM_QDRANT_API_KEY") or "").strip() or None
    try:
        client = QdrantClient(url=url, api_key=api_key)
    except Exception as exc:
        if _is_qdrant_shard_panic(str(exc)):
            raise RuntimeError(
                f"Qdrant at {url} cannot deserialize its shard metadata "
                f"(probable version mismatch or corrupt volume). "
                "Recovery: stop the qdrant container, back up the volume, then either "
                "(a) downgrade the qdrant image to the version that wrote the volume, "
                "or (b) run `docker volume rm` to start clean. "
                "See docs/operations/RUNBOOK.md → Qdrant recovery."
            ) from exc
        raise
    return _QdrantAdapter(client)


class _QdrantAdapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def list_collections(self) -> list[str]:
        result = self._client.get_collections()
        collections = getattr(result, "collections", None) or []
        return [getattr(item, "name", "") for item in collections if getattr(item, "name", "")]

    def _ensure(self, collection: str, vector_size: int) -> None:
        try:
            self._client.get_collection(collection)
            return
        except Exception:
            pass
        from qdrant_client.http import models as qmodels  # type: ignore[import-not-found]

        if vector_size <= 0:
            vector_size = 1
        self._client.recreate_collection(
            collection_name=collection,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=qmodels.Distance.COSINE
            ),
        )

    def upsert(
        self,
        collection: str,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        from qdrant_client.http import models as qmodels  # type: ignore[import-not-found]

        self._ensure(collection, len(vector))
        point = qmodels.PointStruct(id=point_id, vector=vector or [0.0], payload=payload)
        self._client.upsert(collection_name=collection, points=[point])
        record_activity(
            "qdrant_ops",
            {
                "op": "upsert",
                "backend": "qdrant",
                "collection": collection,
                "point_id": point_id,
                "vector_dim": len(vector),
            },
        )

    def search(
        self, collection: str, query_vector: list[float], limit: int = 10
    ) -> list[VectorHit]:
        if not query_vector or limit <= 0:
            return []
        hits = self._client.search(
            collection_name=collection, query_vector=query_vector, limit=limit
        )
        results = [
            VectorHit(
                id=str(getattr(h, "id", "")),
                score=float(getattr(h, "score", 0.0)),
                payload=dict(getattr(h, "payload", {}) or {}),
            )
            for h in (hits or [])
        ]
        record_activity(
            "qdrant_ops",
            {
                "op": "search",
                "backend": "qdrant",
                "collection": collection,
                "vector_dim": len(query_vector),
                "limit": limit,
                "hit_count": len(results),
            },
        )
        return results

    def scroll(self, collection: str, limit: int = 100) -> list[VectorHit]:
        if limit <= 0:
            return []
        result = self._client.scroll(collection_name=collection, limit=limit)
        points = result[0] if isinstance(result, tuple) else result
        return [
            VectorHit(
                id=str(getattr(p, "id", "")),
                score=0.0,
                payload=dict(getattr(p, "payload", {}) or {}),
            )
            for p in (points or [])
        ]

    def delete(self, collection: str, point_ids: Iterable[str]) -> None:
        from qdrant_client.http import models as qmodels  # type: ignore[import-not-found]

        ids = [pid for pid in point_ids if pid]
        if not ids:
            return
        self._client.delete(
            collection_name=collection,
            points_selector=qmodels.PointIdsList(points=ids),
        )

    def count(self, collection: str) -> int:
        result = self._client.count(collection_name=collection, exact=True)
        return int(getattr(result, "count", 0))


def get_vector_store() -> Any:
    global _store_instance
    with _store_lock:
        if _store_instance is None:
            _store_instance = _build_store()
        return _store_instance


__all__ = [
    "InMemoryVectorStore",
    "VectorHit",
    "get_vector_store",
]
