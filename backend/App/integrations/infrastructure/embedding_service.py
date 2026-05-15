from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional, Protocol

from backend.App.shared.infrastructure.cache import ThreadSafeLRUDict, hash_cache_key

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str:
        ...

    @property
    def dim(self) -> int:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class NullEmbeddingProvider:
    @property
    def name(self) -> str:
        return "null"

    @property
    def dim(self) -> int:
        return 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class LocalSentenceTransformersProvider:
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
            self._dim = int(self._model.get_embedding_dimension() or 0)

    @property
    def dim(self) -> int:
        self._ensure_loaded()
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        try:
            vectors = self._model.encode(
                texts,
                batch_size=_batch_size(),
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
        except Exception as exc:
            raise EmbeddingError(
                f"local sentence-transformers embed failed: {exc}"
            ) from exc
        return [list(map(float, v)) for v in vectors]


class OpenAIEmbeddingsProvider:
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
                raise EmbeddingError(
                    f"openai /embeddings call failed for batch starting at {i}: {exc}"
                ) from exc
            for item in resp.data:
                vec = list(map(float, item.embedding or []))
                if vec and not self._dim:
                    self._dim = len(vec)
                out.append(vec)
        return out


class _CachedProvider:
    def __init__(self, inner: EmbeddingProvider, capacity: int) -> None:
        self._inner = inner
        self._capacity = max(0, int(capacity))
        self._cache: ThreadSafeLRUDict[str, list[float]] = ThreadSafeLRUDict(
            max_size=self._capacity or 1,
        )

    @property
    def name(self) -> str:
        return f"{self._inner.name}+cache" if self._capacity else self._inner.name

    @property
    def dim(self) -> int:
        return self._inner.dim

    @staticmethod
    def _key(text: str) -> str:
        return hash_cache_key("", text, digest_len=64)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._capacity <= 0:
            return self._inner.embed(texts)

        keys = [self._key(t) for t in texts]
        hits = self._cache.get_many(keys)
        misses_idx = [i for i, k in enumerate(keys) if k not in hits]
        misses_texts = [texts[i] for i in misses_idx]
        computed: list[list[float]] = (
            self._inner.embed(misses_texts) if misses_texts else []
        )
        for j, i in enumerate(misses_idx):
            vec = computed[j] if j < len(computed) else []
            self._cache.set(keys[i], vec)
            hits[keys[i]] = vec
        return [hits.get(k, []) for k in keys]


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
            "nomic-embed-text",
        )
        return OpenAIEmbeddingsProvider(model)
    raise ValueError(
        f"unknown SWARM_EMBEDDING_PROVIDER={provider_id!r} "
        "(supported: local, openai, null, auto)"
    )


_provider_singleton: Optional[EmbeddingProvider] = None
_singleton_lock = threading.Lock()


def get_embedding_provider() -> EmbeddingProvider:
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
