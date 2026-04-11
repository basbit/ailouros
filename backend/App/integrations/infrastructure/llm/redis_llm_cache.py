"""Infrastructure: Redis-backed LLM response cache.

Implements ``LLMCachePort`` using Redis as the backing store.
The key hashing algorithm is identical to the one in ``cache.py`` so that
existing cached entries remain valid after migration.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from backend.App.integrations.domain.ports import LLMCachePort

logger = logging.getLogger(__name__)


class RedisLLMCache(LLMCachePort):
    """Redis-backed LLM response cache.

    Args:
        client: A connected ``redis.Redis`` instance (decode_responses=False).
        ttl_sec: TTL for cached entries in seconds.
    """

    def __init__(self, client: Any, ttl_sec: int = 3600) -> None:
        self._client = client
        self._ttl_sec = ttl_sec

    def make_key(self, messages: list[dict[str, Any]], model: str, temperature: float) -> str:
        """Derive a deterministic SHA-256 cache key.

        Uses the same hashing strategy as the module-level ``cache_key()``
        function so that previously cached entries remain valid.

        Args:
            messages: List of chat message dicts (role + content).
            model: Model identifier string.
            temperature: Sampling temperature.

        Returns:
            A ``swarm:llmc:<hex>`` cache key string.
        """
        h = hashlib.sha256()
        h.update(model.encode("utf-8"))
        h.update(b"\0")
        h.update(repr(temperature).encode("utf-8"))
        h.update(b"\0")
        for m in messages:
            role = str(m.get("role") or "")
            h.update(role.encode("utf-8"))
            h.update(b"\0")
            content = m.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, sort_keys=True, ensure_ascii=False)
            inner = hashlib.sha256(content.encode("utf-8"))
            h.update(inner.digest())
            h.update(b"\0")
        return f"swarm:llmc:{h.hexdigest()}"

    def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        """Return the cached ``(text, usage)`` pair or ``None`` on cache miss.

        Args:
            key: Cache key produced by ``make_key``.

        Returns:
            Tuple of (response_text, usage_dict) if cached, else ``None``.
        """
        try:
            cached_bytes = self._client.get(key)
            if cached_bytes is None:
                return None
            data = json.loads(cached_bytes.decode("utf-8"))
            return (data["text"], data["usage"])
        except Exception as exc:
            logger.debug("RedisLLMCache.get failed (key=%s): %s", key, exc)
            return None

    def set(self, key: str, text: str, usage: dict[str, Any]) -> None:
        """Store a response in Redis with the configured TTL.

        Args:
            key: Cache key produced by ``make_key``.
            text: LLM response text.
            usage: Usage metadata dict (e.g. token counts).
        """
        try:
            payload = json.dumps({"text": text, "usage": usage}, ensure_ascii=False)
            self._client.setex(key, self._ttl_sec, payload)
        except Exception as exc:
            logger.debug("RedisLLMCache.set failed (key=%s): %s", key, exc)
