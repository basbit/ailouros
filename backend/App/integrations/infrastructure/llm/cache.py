import hashlib
import json
import logging
import os
from typing import Any

from backend.App.integrations.domain.ports import LLMCachePort

try:
    import redis as _redis_mod  # type: ignore[import-not-found,no-redef]
except ImportError:
    _redis_mod = None  # type: ignore[assignment]

_DEFAULT_REDIS_URL: str = (
    os.getenv("REDIS_URL", "redis://localhost:6379/0") or "redis://localhost:6379/0"
).strip()

logger = logging.getLogger(__name__)

__all__ = ["cache_enabled", "cache_key", "get_cached", "set_cached"]

_cached_redis_client = None
_cached_redis_url: str = ""

_LRU_MAX_SIZE = 256
_lru_cache: "dict[str, tuple[str, dict]]" = {}
_lru_keys: "list[str]" = []
_redis_unavailable = False

_default_cache: "LLMCachePort | None" = None
_default_cache_url: str = ""
_default_cache_ttl: int = 0


def cache_ttl() -> int:
    return int(os.getenv("SWARM_LLM_CACHE_TTL", "300"))


def cache_enabled() -> bool:
    return cache_ttl() > 0


def _redis_socket_timeout_params() -> dict[str, Any]:
    def _f(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            parsed_value = float(raw)
            return parsed_value if parsed_value > 0 else default
        except ValueError:
            return default

    return {
        "socket_connect_timeout": _f("REDIS_SOCKET_CONNECT_TIMEOUT", 5.0),
        "socket_timeout": _f("REDIS_SOCKET_TIMEOUT", 30.0),
    }


def _redis_client():
    global _cached_redis_client, _cached_redis_url
    if _redis_mod is None:
        return None
    url = os.getenv("SWARM_REDIS_URL") or os.getenv("REDIS_URL") or _DEFAULT_REDIS_URL
    if _cached_redis_client is not None and url == _cached_redis_url:
        return _cached_redis_client
    if _cached_redis_client is not None:
        try:
            _cached_redis_client.close()
        except Exception as exc:
            logger.debug("LLM cache: failed to close stale Redis client: %s", exc)
    try:
        _cached_redis_client = _redis_mod.Redis.from_url(
            url,
            decode_responses=False,
            **_redis_socket_timeout_params(),
        )
        _cached_redis_url = url
        return _cached_redis_client
    except Exception as exc:
        logger.debug("LLM cache Redis client creation failed (%s): cache disabled.", exc)
        _cached_redis_client = None
        return None


def _get_default_cache() -> "LLMCachePort | None":
    global _default_cache, _default_cache_url, _default_cache_ttl
    if not cache_enabled():
        return None
    client = _redis_client()
    if client is None:
        return None
    url = _cached_redis_url
    ttl = cache_ttl()
    if _default_cache is not None and url == _default_cache_url and ttl == _default_cache_ttl:
        return _default_cache
    from backend.App.integrations.infrastructure.llm.redis_llm_cache import RedisLLMCache
    _default_cache = RedisLLMCache(client, ttl_sec=ttl)
    _default_cache_url = url
    _default_cache_ttl = ttl
    return _default_cache


def cache_key(messages: list[dict], model: str, temperature: float) -> str:
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
    digest = h.hexdigest()
    return f"swarm:llmc:{digest}"


def _lru_get(key: str) -> "tuple[str, dict] | None":
    return _lru_cache.get(key)


def _lru_set(key: str, text: str, usage: dict) -> None:
    global _lru_keys
    if key in _lru_cache:
        _lru_keys = [k for k in _lru_keys if k != key]
    _lru_cache[key] = (text, usage)
    _lru_keys.append(key)
    while len(_lru_keys) > _LRU_MAX_SIZE:
        evicted = _lru_keys.pop(0)
        _lru_cache.pop(evicted, None)


def get_cached(key: str) -> "tuple[str, dict] | None":
    global _redis_unavailable
    if not _redis_unavailable:
        try:
            client = _redis_client()
            if client is not None:
                cached_bytes = client.get(key)
                if cached_bytes is not None:
                    data = json.loads(cached_bytes.decode("utf-8"))
                    return (data["text"], data["usage"])
                return None
        except Exception as exc:
            logger.warning(
                "LLM cache: Redis unavailable — switching to in-memory LRU cache "
                "(cache will be lost on restart). Error: %s", exc,
            )
            _redis_unavailable = True
    return _lru_get(key)


def set_cached(key: str, text: str, usage: dict) -> None:
    global _redis_unavailable
    if not _redis_unavailable:
        try:
            client = _redis_client()
            if client is not None:
                ttl = cache_ttl()
                payload = json.dumps({"text": text, "usage": usage}, ensure_ascii=False)
                client.setex(key, ttl, payload)
                return
        except Exception as exc:
            logger.warning(
                "LLM cache: Redis unavailable — switching to in-memory LRU cache. Error: %s", exc,
            )
            _redis_unavailable = True
    _lru_set(key, text, usage)
