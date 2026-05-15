from __future__ import annotations

import time
from typing import Callable, Optional

from backend.App.shared.health.probe import ProbeResult


class QdrantProbe:
    subsystem: str = "qdrant"

    def __init__(self, store_getter: Optional[Callable[[], object]] = None) -> None:
        self._store_getter = store_getter

    def _default_store(self) -> object:
        from backend.App.integrations.infrastructure.qdrant_client import (
            get_vector_store,
        )

        return get_vector_store()

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        try:
            store = (self._store_getter or self._default_store)()
        except ImportError as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"vector store module unavailable: {exc}",
                metadata={},
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"vector store init failed: {type(exc).__name__}: {exc}",
                metadata={},
            )

        backend_name = type(store).__name__
        collections_count = 0
        get_collections = getattr(store, "list_collections", None) or getattr(
            store, "collections", None
        )
        try:
            if callable(get_collections):
                items = get_collections()
                collections_count = len(list(items)) if items is not None else 0
            elif isinstance(get_collections, (list, tuple, set)):
                collections_count = len(get_collections)
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"list_collections failed: {type(exc).__name__}: {exc}",
                metadata={"backend": backend_name},
            )

        elapsed = (time.perf_counter() - start) * 1000.0
        metadata = {
            "backend": backend_name,
            "collections": str(collections_count),
        }
        if backend_name == "InMemoryVectorStore":
            return ProbeResult(
                subsystem=self.subsystem,
                status="degraded",
                latency_ms=elapsed,
                detail="using in-memory vector store; restarts lose data",
                metadata=metadata,
            )
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail=f"backend={backend_name} collections={collections_count}",
            metadata=metadata,
        )


__all__ = ["QdrantProbe"]
