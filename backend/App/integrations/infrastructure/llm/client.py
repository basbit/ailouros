from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from backend.App.integrations.infrastructure.llm.openai_client_pool import (
    OpenAIClientPool,
    _build_client,
    _is_local_openai_compat_base_url,
    _resolve_openai_max_retries,
    openai_http_timeout_seconds,
    _default_pool,
)

from backend.App.integrations.infrastructure.llm.token_tracker import (
    ThreadTokenTracker,
    _accumulate_thread_usage,
    get_and_reset_thread_usage,
    reset_thread_usage,
    thread_usage_tracking,
    _default_tracker,
)

from backend.App.integrations.infrastructure.llm.router import (
    LLMRouter,
    _LOCAL_LLM_HTTP_LOCK,
    _get_default_router,
    _local_llm_serialize_http_enabled,
    _local_llm_serialize_lock_acquire_timeout_sec,
    merge_openai_compat_max_tokens,
)

from openai import OpenAI

from backend.App.integrations.infrastructure.llm.cache import (
    cache_enabled,
    cache_key,
    get_cached,
    set_cached,
)

from backend.App.integrations.infrastructure.llm.providers import (
    _ask_anthropic,
    _ask_litellm,
    _build_anthropic_client,
    _is_cloud_model,
    _litellm_enabled,
    _use_anthropic_backend,
)

from backend.App.integrations.infrastructure.llm.config import OLLAMA_BASE_URL
from backend.App.integrations.infrastructure.llm.prompt_size import (
    estimate_chat_request_size,
    log_request_size,
    maybe_warn_context_limit,
)

import threading as _threading
import httpx as _httpx

logger = logging.getLogger(__name__)


def _make_openai_client_uncached(*, base_url: str, api_key: str) -> "OpenAI":
    http_timeout = openai_http_timeout_seconds()
    if http_timeout is not None:
        timeout = _httpx.Timeout(http_timeout, connect=min(30.0, float(http_timeout)))
    elif _is_local_openai_compat_base_url(base_url):
        timeout = _httpx.Timeout(None, connect=10.0)
    else:
        timeout = _httpx.Timeout(600.0, connect=5.0)
    disable_keepalive = os.getenv("SWARM_HTTPX_DISABLE_KEEPALIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if disable_keepalive:
        limits = _httpx.Limits(max_keepalive_connections=0)
    else:
        limits = _httpx.Limits(max_keepalive_connections=20, keepalive_expiry=5.0)
    http_client = _httpx.Client(timeout=timeout, limits=limits, follow_redirects=True)
    max_retries = _resolve_openai_max_retries(base_url)
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=max_retries,
        http_client=http_client,
    )


__all__ = [
    "ask_model",
    "make_openai_client",
    "OpenAIClientPool",
    "_build_client",
    "_is_local_openai_compat_base_url",
    "_resolve_openai_max_retries",
    "openai_http_timeout_seconds",
    "_default_pool",
    "ThreadTokenTracker",
    "_accumulate_thread_usage",
    "get_and_reset_thread_usage",
    "reset_thread_usage",
    "thread_usage_tracking",
    "_default_tracker",
    "LLMRouter",
    "_LOCAL_LLM_HTTP_LOCK",
    "_get_default_router",
    "_local_llm_serialize_http_enabled",
    "_local_llm_serialize_lock_acquire_timeout_sec",
    "merge_openai_compat_max_tokens",
    "cache_enabled",
    "cache_key",
    "get_cached",
    "set_cached",
    "_ask_anthropic",
    "_ask_litellm",
    "_build_anthropic_client",
    "_is_cloud_model",
    "_litellm_enabled",
    "_use_anthropic_backend",
    "OLLAMA_BASE_URL",
    "estimate_chat_request_size",
    "log_request_size",
    "maybe_warn_context_limit",
]

_openai_client_cache: dict[tuple[str, str], "OpenAI"] = {}
_openai_client_cache_lock = _threading.Lock()


def make_openai_client(*, base_url: str, api_key: str) -> "OpenAI":
    import backend.App.integrations.infrastructure.llm.client as _self

    cache_key_pair = (base_url, api_key)
    with _openai_client_cache_lock:
        cached = _openai_client_cache.get(cache_key_pair)
        if cached is not None:
            return cached
        stale_keys = [k for k in _openai_client_cache if k[0] == base_url and k[1] != api_key]
        for sk in stale_keys:
            old_client = _openai_client_cache.pop(sk)
            try:
                old_client.close()
            except Exception as close_exc:
                logger.debug("Failed to close stale OpenAI client: %s", close_exc)
        new_client = _self._make_openai_client_uncached(base_url=base_url, api_key=api_key)
        _openai_client_cache[cache_key_pair] = new_client
        return new_client


def ask_model(
    messages: list[dict[str, str]],
    model: str = "",
    temperature: float = 0.2,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    anthropic_api_key: Optional[str] = None,
    anthropic_base_url: Optional[str] = None,
    llm_route: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    import backend.App.integrations.infrastructure.llm.client as _self

    backend_path = (
        "litellm"
        if _self._litellm_enabled()
        else (
            "anthropic"
            if _self._use_anthropic_backend(model, llm_route)
            else "openai_compat"
        )
    )
    logger.info(
        "ask_model: model=%r backend_path=%s llm_cache=%s",
        model,
        backend_path,
        _self.cache_enabled(),
    )

    key = None
    if _self.cache_enabled():
        t_key0 = time.perf_counter()
        key = _self.cache_key(messages, model, temperature)
        key_dt = time.perf_counter() - t_key0
        if key_dt > 1.0:
            logger.warning(
                "LLM cache_key took %.1fs (very large user/system in messages?); "
                "previously this serialised the entire prompt with json.dumps — "
                "see agents/llm_cache.cache_key",
                key_dt,
            )
        t_get0 = time.perf_counter()
        cached = _self.get_cached(key)
        get_dt = time.perf_counter() - t_get0
        if get_dt > 2.0:
            logger.warning(
                "LLM cache Redis GET took %.1fs — check Redis and REDIS_SOCKET_TIMEOUT",
                get_dt,
            )
        if cached is not None:
            logger.info(
                "LLM cache HIT — skipping HTTP call to local LLM/Ollama (response from Redis cache)"
            )
            text, cached_usage = cached
            return (text, {**cached_usage, "cached": True})

    if _self._litellm_enabled():
        eff_api_key = (anthropic_api_key or api_key or "").strip() or None
        eff_base_url = (anthropic_base_url or base_url or "").strip() or None
        litellm_kwargs = {k: v for k, v in kwargs.items() if k not in (
            "llm_route", "anthropic_api_key", "anthropic_base_url",
        )}
        max_tokens = litellm_kwargs.pop("max_tokens", None)
        text, usage = _self._ask_litellm(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=eff_api_key,
            base_url=eff_base_url,
            **litellm_kwargs,
        )
        if key is not None:
            _self.set_cached(key, text, usage)
        return (text, usage)

    if _self._use_anthropic_backend(model, llm_route):
        text, usage = _self._ask_anthropic(
            messages=messages,
            model=model,
            temperature=temperature,
            anthropic_api_key=anthropic_api_key,
            anthropic_base_url=anthropic_base_url,
            **kwargs,
        )
        if key is not None:
            _self.set_cached(key, text, usage)
        return (text, usage)

    kwargs.pop("llm_route", None)
    _size = _self.estimate_chat_request_size(messages)
    _self.log_request_size(model, _size)
    _self.maybe_warn_context_limit(model, _size)
    resolved_base_url = (
        base_url or os.getenv("OPENAI_BASE_URL", _self.OLLAMA_BASE_URL) or ""
    ).strip()
    if not resolved_base_url:
        resolved_base_url = _self.OLLAMA_BASE_URL
    client = _self._build_client(base_url=base_url, api_key=api_key)
    create_kwargs = _self.merge_openai_compat_max_tokens(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        },
        base_url=resolved_base_url,
    )

    if "timeout" not in create_kwargs:
        from backend.App.integrations.infrastructure.llm.router import (
            _local_llm_request_timeout_sec as _resolve_timeout,
        )
        _t = _resolve_timeout(resolved_base_url)
        if _t is not None:
            create_kwargs["timeout"] = _t

    if "extra_body" not in create_kwargs:
        from backend.App.integrations.infrastructure.llm.router import (
            _local_llm_reasoning_budget as _resolve_reasoning_budget,
        )
        _budget = _resolve_reasoning_budget(model, resolved_base_url)
        if _budget is not None:
            create_kwargs["extra_body"] = {"thinking_budget_tokens": _budget}
            logger.info(
                "ask_model: local reasoning budget cap: thinking_budget_tokens=%d model=%r",
                _budget,
                model,
            )

    def _chat_create():
        return client.chat.completions.create(**create_kwargs)

    logger.info(
        "OpenAI-compatible POST /chat/completions: model=%r base=%s msgs=%d",
        model,
        resolved_base_url[:96],
        len(messages),
    )
    if _self._local_llm_serialize_http_enabled(resolved_base_url):
        tmo = _self._local_llm_serialize_lock_acquire_timeout_sec()
        if tmo is None:
            with _self._LOCAL_LLM_HTTP_LOCK:
                response = _chat_create()
        else:
            acquired = _self._LOCAL_LLM_HTTP_LOCK.acquire(timeout=tmo)
            if not acquired:
                raise RuntimeError(
                    f"SWARM_LOCAL_LLM_SERIALIZE: could not acquire HTTP lock within {tmo}s "
                    "(another thread is inside chat.completions to the local LLM). "
                    "Disable SERIALIZE or set SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC."
                )
            try:
                response = _chat_create()
            finally:
                _self._LOCAL_LLM_HTTP_LOCK.release()
    else:
        response = _chat_create()
    if not response.choices:
        raise ValueError(f"LLM returned empty choices list (model={model})")
    text = response.choices[0].message.content or ""
    usage_obj = response.usage
    usage = {
        "input_tokens": (getattr(usage_obj, "prompt_tokens", None) or 0),
        "output_tokens": (getattr(usage_obj, "completion_tokens", None) or 0),
        "model": model,
        "cached": False,
    }
    if key is not None:
        _self.set_cached(key, text, usage)
    return (text, usage)


def chat_completion_text(
    messages: list[dict[str, str]],
    model: str = "",
    **kwargs: Any,
) -> str:
    text, _ = ask_model(messages=messages, model=model, **kwargs)
    return text
