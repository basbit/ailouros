from __future__ import annotations

import time
from typing import Callable, Optional

from backend.App.shared.health.probe import ProbeResult


class EmbeddingProbe:
    subsystem: str = "embedding"

    def __init__(
        self,
        provider_getter: Optional[Callable[[], object]] = None,
        cache_size_getter: Optional[Callable[[], int]] = None,
    ) -> None:
        self._provider_getter = provider_getter
        self._cache_size_getter = cache_size_getter

    def _default_provider(self) -> object:
        from backend.App.integrations.infrastructure.embedding_service import (
            get_embedding_provider,
        )

        return get_embedding_provider()

    def _default_cache_size(self) -> int:
        import os

        try:
            return max(0, int(os.getenv("SWARM_EMBEDDING_CACHE_SIZE", "1024")))
        except ValueError:
            return 0

    def probe(self) -> ProbeResult:
        start = time.perf_counter()
        try:
            provider = (self._provider_getter or self._default_provider)()
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"provider init failed: {type(exc).__name__}: {exc}",
                metadata={},
            )

        provider_name = str(getattr(provider, "name", "unknown"))
        try:
            vectors = provider.embed(["health"])  # type: ignore[attr-defined]
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000.0
            return ProbeResult(
                subsystem=self.subsystem,
                status="error",
                latency_ms=elapsed,
                detail=f"embed call failed: {type(exc).__name__}: {exc}",
                metadata={"provider": provider_name},
            )

        elapsed = (time.perf_counter() - start) * 1000.0
        cache_size = (self._cache_size_getter or self._default_cache_size)()
        metadata = {
            "provider": provider_name,
            "cache_size": str(cache_size),
            "dim": str(len(vectors[0]) if vectors and vectors[0] else 0),
        }
        if not vectors or not vectors[0]:
            return ProbeResult(
                subsystem=self.subsystem,
                status="degraded",
                latency_ms=elapsed,
                detail="provider returned empty vector",
                metadata=metadata,
            )
        return ProbeResult(
            subsystem=self.subsystem,
            status="ok",
            latency_ms=elapsed,
            detail=f"provider={provider_name} dim={metadata['dim']}",
            metadata=metadata,
        )


__all__ = ["EmbeddingProbe"]
