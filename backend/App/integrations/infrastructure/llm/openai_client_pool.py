"""OpenAIClientPool: cached OpenAI client per (base_url, api_key) pair.

Extracted from client.py to give the connection-pool lifecycle a dedicated home.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import httpx
from openai import OpenAI

from backend.App.integrations.infrastructure.llm.config import OLLAMA_BASE_URL

logger = logging.getLogger(__name__)


def openai_http_timeout_seconds() -> Optional[float]:
    """Optionally cap response wait time (Ollama/Gemini OpenAI-compat). Empty = SDK default."""
    env_value = os.getenv("SWARM_OPENAI_HTTP_TIMEOUT_SEC", "").strip().lower()
    if env_value in ("", "0", "none", "unlimited", "off"):
        return None
    try:
        return float(env_value)
    except ValueError:
        return None


def _is_local_openai_compat_base_url(base_url: str) -> bool:
    lower_base_url = (base_url or "").lower()
    return (
        "127.0.0.1" in lower_base_url
        or "localhost" in lower_base_url
        or ":11434" in lower_base_url
        or ":1234" in lower_base_url
        or "host.docker.internal" in lower_base_url
    )


def _resolve_openai_max_retries(base_url: str) -> int:
    """SDK default is max_retries=2: on ReadTimeout the full chat completion is retried.

    For Ollama/LM Studio this doubles the load and causes a client-side hang
    while the first request is still running.
    """
    env_value = os.getenv("SWARM_OPENAI_MAX_RETRIES", "").strip()
    if env_value.isdigit():
        return max(0, int(env_value))
    if _is_local_openai_compat_base_url(base_url):
        return 0
    return 2


class OpenAIClientPool:
    """Thread-safe pool of cached OpenAI clients keyed by (base_url, api_key).

    Caching avoids leaking ``httpx.Client`` connection pools when
    ``ask_model`` is called once per request.  A new client is created
    (and the old one closed) only when the key pair changes.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], OpenAI] = {}
        self._lock = threading.Lock()

    def _make_client(self, *, base_url: str, api_key: str) -> OpenAI:
        """Create a new OpenAI client with a fresh httpx.Client connection pool."""
        return _make_openai_client_uncached(base_url=base_url, api_key=api_key)

    def get(self, base_url: str, api_key: str) -> OpenAI:
        """Return a cached client for *(base_url, api_key)*.

        Args:
            base_url: OpenAI-compatible endpoint URL.
            api_key: API key string.

        Returns:
            A ready-to-use :class:`openai.OpenAI` client.
        """
        cache_key_pair = (base_url, api_key)
        with self._lock:
            cached = self._cache.get(cache_key_pair)
            if cached is not None:
                return cached
            # Evict stale entries whose URL matches but key changed.
            stale_keys = [k for k in self._cache if k[0] == base_url and k[1] != api_key]
            for sk in stale_keys:
                old_client = self._cache.pop(sk)
                try:
                    old_client.close()
                except Exception as close_exc:
                    logger.debug("OpenAIClientPool: failed to close stale client: %s", close_exc)
            new_client = self._make_client(base_url=base_url, api_key=api_key)
            self._cache[cache_key_pair] = new_client
            return new_client

    def close_all(self) -> None:
        """Close all pooled clients and clear the cache."""
        with self._lock:
            for client in self._cache.values():
                try:
                    client.close()
                except Exception as exc:
                    logger.debug("OpenAIClientPool.close_all: error closing client: %s", exc)
            self._cache.clear()


# ---------------------------------------------------------------------------
# Module-level singleton (backward-compat; existing code calls make_openai_client)
# ---------------------------------------------------------------------------

_default_pool = OpenAIClientPool()


def _make_openai_client_uncached(*, base_url: str, api_key: str) -> OpenAI:
    """Create a new OpenAI client with a fresh httpx.Client connection pool.

    Standalone module-level function so that tests can patch
    ``openai_client_pool.OpenAI`` or ``openai_client_pool._make_openai_client_uncached``
    and intercept client construction.
    """
    http_timeout = openai_http_timeout_seconds()
    if http_timeout is not None:
        timeout = httpx.Timeout(http_timeout, connect=min(30.0, float(http_timeout)))
    elif _is_local_openai_compat_base_url(base_url):
        timeout = httpx.Timeout(None, connect=10.0)
    else:
        timeout = httpx.Timeout(600.0, connect=5.0)
    disable_keepalive = os.getenv("SWARM_HTTPX_DISABLE_KEEPALIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if disable_keepalive:
        limits = httpx.Limits(max_keepalive_connections=0)
    else:
        limits = httpx.Limits(max_keepalive_connections=20, keepalive_expiry=5.0)
    http_client = httpx.Client(timeout=timeout, limits=limits, follow_redirects=True)
    max_retries = _resolve_openai_max_retries(base_url)
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=max_retries,
        http_client=http_client,
    )


def make_openai_client(*, base_url: str, api_key: str) -> OpenAI:
    """Return a cached OpenAI client for the given (base_url, api_key) pair.

    .. deprecated::
        Prefer using :class:`OpenAIClientPool` directly.
    """
    return _default_pool.get(base_url, api_key)


def _build_client(base_url: Optional[str] = None, api_key: Optional[str] = None) -> OpenAI:
    base_url_final = base_url or os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL)
    api_key_final = api_key or os.getenv("OPENAI_API_KEY", "ollama")
    return make_openai_client(base_url=base_url_final, api_key=api_key_final)
