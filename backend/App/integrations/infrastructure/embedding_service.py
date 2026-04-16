"""Shared embedding service (C-1).

Unified interface for generating text embeddings across the backend.
Unblocks future work on §15 semantic memory, §17 smart context and
§13.1 semantic wiki search. The service is intentionally thin — it
exposes a single `embed` method and a factory that picks a provider
from env without pulling in any hard dependencies.

Providers
---------

- ``local``   — sentence-transformers (CPU/GPU, offline). Model defaults
                 to ``sentence-transformers/all-MiniLM-L6-v2`` (384-dim,
                 small, good for search). Optional dependency.
- ``openai``  — any OpenAI-compatible ``/embeddings`` endpoint
                 (OpenAI, Ollama via ``/v1/embeddings``, LM Studio, etc).
                 Honours ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``.
- ``null``    — deterministic empty provider. Returns ``[]``. Used when
                 no provider is configured; callers must treat an empty
                 list as "embeddings unavailable".

Selection order:

1. Explicit ``SWARM_EMBEDDING_PROVIDER`` env var (``local`` | ``openai`` | ``null``).
2. Auto: ``local`` if ``sentence_transformers`` is importable, else
   ``openai`` if ``OPENAI_API_KEY`` or ``OPENAI_BASE_URL`` is set, else ``null``.

Env vars
--------

- ``SWARM_EMBEDDING_PROVIDER``  — ``local`` | ``openai`` | ``null`` | ``auto``
  (default: ``auto``).
- ``SWARM_EMBEDDING_MODEL``     — model name for the chosen provider.
- ``SWARM_EMBEDDING_CACHE_SIZE``— in-process LRU cache capacity
  (default ``1024``). Set to ``0`` to disable caching.
- ``SWARM_EMBEDDING_BATCH_SIZE``— batching size for provider calls
  (default ``16``).

Callers read a singleton via :func:`get_embedding_provider`. Reset the
singleton with :func:`reset_embedding_provider` in tests.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    Implementations must return a list of float vectors of identical length.
    An empty list is a valid "I cannot embed this" response — callers
    fall back to keyword/TF-IDF search.

    ``name`` and ``dim`` are declared as read-only properties so concrete
    providers can compute them lazily (e.g. after loading the model).
    """

    @property
    def name(self) -> str:
        ...

    @property
    def dim(self) -> int:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


class NullEmbeddingProvider:
    """No-op provider. Returns empty vectors; callers skip semantic ranking."""

    @property
    def name(self) -> str:
        return "null"

    @property
    def dim(self) -> int:
        return 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class LocalSentenceTransformersProvider:
    """sentence-transformers provider.

    Loads the model lazily on first call so import cost is paid only when
    actually used. Model is cached on the instance.
    """

    @property
    def name(self) -> str:
        return "local"

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: Any = None
        self._dim: int = 0
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "SWARM_EMBEDDING_PROVIDER=local requires sentence-transformers. "
                    "Install with: pip install sentence-transformers"
                ) from exc
            logger.info("embedding_service: loading local model %r", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            self._dim = int(self._model.get_sentence_embedding_dimension() or 0)

    @property
    def dim(self) -> int:
        self._ensure_loaded()
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            self._ensure_loaded()
            vectors = self._model.encode(
                texts,
                batch_size=_batch_size(),
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return [list(map(float, v)) for v in vectors]
        except Exception as exc:
            logger.warning(
                "embedding_service: local provider embed failed (%s) — "
                "returning empty vectors so callers fall back to keyword search",
                exc,
            )
            return [[] for _ in texts]


class OpenAIEmbeddingsProvider:
    """OpenAI-compatible ``/embeddings`` provider.

    Works with OpenAI, Ollama (``POST /v1/embeddings``), LM Studio and any
    other provider that speaks the OpenAI embeddings protocol.
    """

    @property
    def name(self) -> str:
        return "openai"

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._dim: int = 0
        self._client: Any = None
        self._client_lock = threading.Lock()

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        with self._client_lock:
            if self._client is not None:
                return
            try:
                from openai import OpenAI  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "SWARM_EMBEDDING_PROVIDER=openai requires the openai SDK. "
                    "Install with: pip install openai"
                ) from exc
            base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1") or ""
            api_key = os.getenv("OPENAI_API_KEY", "ollama") or "ollama"
            self._client = OpenAI(base_url=base_url, api_key=api_key)
            logger.info(
                "embedding_service: openai provider ready (model=%r, base_url=%r)",
                self._model_name, base_url,
            )

    @property
    def dim(self) -> int:
        # We discover dim lazily from the first response; return 0 until then.
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_client()
        batch = _batch_size()
        out: list[list[float]] = []
        for i in range(0, len(texts), batch):
            chunk = texts[i:i + batch]
            try:
                resp = self._client.embeddings.create(
                    model=self._model_name,
                    input=chunk,
                )
            except Exception as exc:
                logger.warning(
                    "embedding_service: openai /embeddings call failed (%s); "
                    "returning empty vectors for this batch",
                    exc,
                )
                out.extend([[] for _ in chunk])
                continue
            for item in resp.data:
                vec = list(map(float, item.embedding or []))
                if vec and not self._dim:
                    self._dim = len(vec)
                out.append(vec)
        return out


# ---------------------------------------------------------------------------
# Cache wrapper
# ---------------------------------------------------------------------------


class _CachedProvider:
    """LRU wrapper around any :class:`EmbeddingProvider`.

    Caches per-text vectors by sha256 key. Missing texts are embedded in a
    single batched call; cached ones are served from memory.
    """

    def __init__(self, inner: EmbeddingProvider, capacity: int) -> None:
        self._inner = inner
        self._capacity = max(0, int(capacity))
        self._cache: dict[str, list[float]] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"{self._inner.name}+cache" if self._capacity else self._inner.name

    @property
    def dim(self) -> int:
        return self._inner.dim

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

    def _remember(self, key: str, vec: list[float]) -> None:
        if self._capacity <= 0:
            return
        with self._lock:
            if key in self._cache:
                return
            self._cache[key] = vec
            self._order.append(key)
            while len(self._order) > self._capacity:
                old = self._order.pop(0)
                self._cache.pop(old, None)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._capacity <= 0:
            return self._inner.embed(texts)

        keys = [self._key(t) for t in texts]
        with self._lock:
            hits: dict[str, list[float]] = {
                k: self._cache[k] for k in keys if k in self._cache
            }
        misses_idx = [i for i, k in enumerate(keys) if k not in hits]
        misses_texts = [texts[i] for i in misses_idx]
        computed: list[list[float]] = self._inner.embed(misses_texts) if misses_texts else []
        for j, i in enumerate(misses_idx):
            vec = computed[j] if j < len(computed) else []
            self._remember(keys[i], vec)
            hits[keys[i]] = vec
        return [hits.get(k, []) for k in keys]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _batch_size() -> int:
    try:
        return max(1, int(os.getenv("SWARM_EMBEDDING_BATCH_SIZE", "16")))
    except ValueError:
        return 16


def _cache_capacity() -> int:
    try:
        return max(0, int(os.getenv("SWARM_EMBEDDING_CACHE_SIZE", "1024")))
    except ValueError:
        return 1024


def _auto_choose_provider_id() -> str:
    """Pick a provider when SWARM_EMBEDDING_PROVIDER is unset or 'auto'."""
    try:
        import sentence_transformers  # noqa: F401  # type: ignore[import-not-found]
        return "local"
    except ImportError:
        pass
    if os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"):
        return "openai"
    return "null"


def _make_provider(provider_id: str) -> EmbeddingProvider:
    if provider_id == "null":
        return NullEmbeddingProvider()
    if provider_id == "local":
        model = os.getenv(
            "SWARM_EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        return LocalSentenceTransformersProvider(model)
    if provider_id == "openai":
        model = os.getenv(
            "SWARM_EMBEDDING_MODEL",
            "nomic-embed-text",  # default that works with Ollama
        )
        return OpenAIEmbeddingsProvider(model)
    raise ValueError(
        f"unknown SWARM_EMBEDDING_PROVIDER={provider_id!r} "
        "(supported: local, openai, null, auto)"
    )


_provider_singleton: Optional[EmbeddingProvider] = None
_singleton_lock = threading.Lock()


def get_embedding_provider() -> EmbeddingProvider:
    """Return the process-wide embedding provider (lazily initialised)."""
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    with _singleton_lock:
        if _provider_singleton is not None:
            return _provider_singleton
        raw = (os.getenv("SWARM_EMBEDDING_PROVIDER") or "auto").strip().lower()
        provider_id = _auto_choose_provider_id() if raw in ("", "auto") else raw
        try:
            inner = _make_provider(provider_id)
        except Exception as exc:
            logger.warning(
                "embedding_service: provider %r failed to initialise (%s) — "
                "falling back to NullEmbeddingProvider",
                provider_id, exc,
            )
            inner = NullEmbeddingProvider()
        provider: EmbeddingProvider = _CachedProvider(inner, _cache_capacity())
        _provider_singleton = provider
        logger.info(
            "embedding_service: initialised provider=%s cache=%d batch=%d",
            provider.name, _cache_capacity(), _batch_size(),
        )
        return provider


def reset_embedding_provider() -> None:
    """Drop the singleton. For tests / env changes at runtime."""
    global _provider_singleton
    with _singleton_lock:
        _provider_singleton = None


__all__ = [
    "EmbeddingProvider",
    "NullEmbeddingProvider",
    "LocalSentenceTransformersProvider",
    "OpenAIEmbeddingsProvider",
    "get_embedding_provider",
    "reset_embedding_provider",
]
