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
    env_value = os.getenv("SWARM_OPENAI_MAX_RETRIES", "").strip()
    if env_value.isdigit():
        return max(0, int(env_value))
    if _is_local_openai_compat_base_url(base_url):
        return 0
    return 2


class OpenAIClientPool:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], OpenAI] = {}
        self._lock = threading.Lock()

    def _make_client(self, *, base_url: str, api_key: str) -> OpenAI:
        return _make_openai_client_uncached(base_url=base_url, api_key=api_key)

    def get(self, base_url: str, api_key: str) -> OpenAI:
        cache_key_pair = (base_url, api_key)
        with self._lock:
            cached = self._cache.get(cache_key_pair)
            if cached is not None:
                return cached
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
        with self._lock:
            for client in self._cache.values():
                try:
                    client.close()
                except Exception as exc:
                    logger.debug("OpenAIClientPool.close_all: error closing client: %s", exc)
            self._cache.clear()


_default_pool = OpenAIClientPool()


def _make_openai_client_uncached(*, base_url: str, api_key: str) -> OpenAI:
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
    return _default_pool.get(base_url, api_key)


def _build_client(base_url: Optional[str] = None, api_key: Optional[str] = None) -> OpenAI:
    base_url_final = base_url or os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL) or ""
    api_key_final = api_key or os.getenv("OPENAI_API_KEY", "ollama") or ""
    return make_openai_client(base_url=base_url_final, api_key=api_key_final)
