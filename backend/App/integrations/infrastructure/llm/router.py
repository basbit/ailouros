"""LLMRouter: unified LLM routing across local, remote, and direct-OpenAI backends.

Extracted from client.py.  The router holds no global mutable state — it
delegates caching to a :class:`LLMCachePort` and client management to an
:class:`OpenAIClientPool`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

from backend.App.integrations.domain.ports import LLMCachePort
from backend.App.integrations.infrastructure.llm.config import OLLAMA_BASE_URL
from backend.App.integrations.infrastructure.llm.openai_client_pool import (
    OpenAIClientPool,
    _is_local_openai_compat_base_url,
)
from backend.App.integrations.infrastructure.llm.prompt_size import (
    estimate_chat_request_size,
    log_request_size,
    maybe_warn_context_limit,
)
from backend.App.integrations.infrastructure.llm.providers import (
    _ask_anthropic,
    _ask_litellm,
    _litellm_enabled,
    _use_anthropic_backend,
)
from backend.App.integrations.infrastructure.llm.token_tracker import (
    _accumulate_thread_usage,
)

logger = logging.getLogger(__name__)

# Single Ollama/LM Studio per machine: two parallel chat completions sometimes stall.
_LOCAL_LLM_HTTP_LOCK = threading.Lock()


def merge_openai_compat_max_tokens(
    call_kwargs: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    """Attach ``max_tokens`` for remote OpenAI-compatible APIs when it was omitted.

    Some gateways (notably OpenRouter) assume a very large completion budget when
    ``max_tokens`` is missing and bill against that ceiling — which yields HTTP 402
    ("need more credits") even though the model context window is 200k+.

    Local Ollama/LM Studio URLs are unchanged unless the caller already passed
    ``max_tokens`` or ``SWARM_OPENAI_COMPAT_MAX_TOKENS`` is set.

    Env:
        ``SWARM_OPENAI_COMPAT_MAX_TOKENS`` — if set to a positive integer, always
        used for remote calls when ``max_tokens`` is absent. Otherwise remote
        defaults to 4096 (conservative for low free-tier credit).
    """
    kwargs_copy = dict(call_kwargs)
    if kwargs_copy.get("max_tokens") is not None:
        return kwargs_copy
    if kwargs_copy.get("max_completion_tokens") is not None:
        return kwargs_copy
    env_value = os.getenv("SWARM_OPENAI_COMPAT_MAX_TOKENS", "").strip()
    if env_value.isdigit() and int(env_value) > 0:
        kwargs_copy["max_tokens"] = int(env_value)
        return kwargs_copy
    if not _is_local_openai_compat_base_url(base_url):
        kwargs_copy["max_tokens"] = 4096
    return kwargs_copy


def _local_llm_serialize_http_enabled(resolved_base_url: str) -> bool:
    if os.getenv("SWARM_LOCAL_LLM_SERIALIZE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return _is_local_openai_compat_base_url(resolved_base_url)


def _local_llm_serialize_lock_acquire_timeout_sec() -> Optional[float]:
    """Empty = wait indefinitely.

    Otherwise raise an explicit RuntimeError instead of blocking the second
    thread forever.
    """
    env_value = os.getenv("SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "").strip()
    if not env_value:
        return None
    try:
        parsed_float = float(env_value)
        return parsed_float if parsed_float > 0 else None
    except ValueError:
        return None


def _local_llm_request_timeout_sec(resolved_base_url: str) -> Optional[float]:
    """Return per-request HTTP timeout (seconds) for local LLM calls.

    Only applied when the target is a local URL (LM Studio / Ollama).
    Set ``SWARM_LOCAL_LLM_TIMEOUT_SEC`` to cap runaway reasoning models.
    Default: no timeout (None).
    """
    if not _is_local_openai_compat_base_url(resolved_base_url):
        return None
    env_value = os.getenv("SWARM_LOCAL_LLM_TIMEOUT_SEC", "").strip()
    if not env_value:
        return None
    try:
        parsed = float(env_value)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


_REASONING_MODEL_KEYWORDS = frozenset(
    ("qwen3", "qwen-3", "think", "-r1", "r1-", "deepseek-r1", "reasoning", "ud-mlx")
)


def _is_reasoning_model(model: str) -> bool:
    """Return True when model name suggests a chain-of-thought / thinking model."""
    lowered = (model or "").lower()
    return any(kw in lowered for kw in _REASONING_MODEL_KEYWORDS)


def _local_llm_reasoning_budget(model: str, resolved_base_url: str) -> Optional[int]:
    """Return ``thinking_budget_tokens`` cap for local reasoning models, or None.

    Set ``SWARM_LOCAL_LLM_REASONING_BUDGET=1024`` to cap reasoning token usage.
    Default: no cap (None).  Only applied to local URLs and known reasoning models.
    """
    if not _is_local_openai_compat_base_url(resolved_base_url):
        return None
    if not _is_reasoning_model(model):
        return None
    env_value = os.getenv("SWARM_LOCAL_LLM_REASONING_BUDGET", "").strip()
    if not env_value:
        return None
    try:
        cap = int(env_value)
        return cap if cap > 0 else None
    except ValueError:
        return None


class LLMRouter:
    """Route LLM requests to local, remote, or direct OpenAI-compatible backends.

    Constructor:
        cache: :class:`LLMCachePort` instance (or ``None`` to disable caching).
        client_pool: :class:`OpenAIClientPool` instance.

    Usage::

        router = LLMRouter(cache=get_cache(), client_pool=OpenAIClientPool())
        text, usage = router.ask(messages, model="your-model-id")
    """

    def __init__(
        self,
        cache: Optional[LLMCachePort],
        client_pool: OpenAIClientPool,
    ) -> None:
        self._cache = cache
        self._pool = client_pool

    def ask(
        self,
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
        """Route a chat completion request to the appropriate backend.

        Args:
            messages: List of chat message dicts (role + content).
            model: Model identifier string.
            temperature: Sampling temperature.
            base_url: Override OpenAI-compatible base URL.
            api_key: Override API key.
            anthropic_api_key: Anthropic API key (used when backend = anthropic).
            anthropic_base_url: Anthropic base URL override.
            llm_route: Explicit backend route override (e.g. ``"anthropic"``).
            **kwargs: Extra kwargs forwarded to the backend.

        Returns:
            Tuple of ``(response_text, usage_dict)``.

        Raises:
            openai.APIConnectionError: when the local LLM backend is unreachable.
            openai.AuthenticationError: on invalid API key.
            anthropic.APIStatusError: on Anthropic API errors.
            RuntimeError: when no provider can be resolved for the given arguments.
        """
        backend_path = (
            "litellm"
            if _litellm_enabled()
            else (
                "anthropic"
                if _use_anthropic_backend(model, llm_route)
                else "openai_compat"
            )
        )
        logger.info(
            "LLMRouter.ask: model=%r backend_path=%s cache=%s",
            model,
            backend_path,
            self._cache is not None,
        )

        key: Optional[str] = None
        if self._cache is not None:
            t_key0 = time.perf_counter()
            key = self._cache.make_key(messages, model, temperature)
            key_dt = time.perf_counter() - t_key0
            if key_dt > 1.0:
                logger.warning(
                    "LLM cache_key took %.1fs (very large user/system in messages?)", key_dt
                )
            t_get0 = time.perf_counter()
            cached = self._cache.get(key)
            get_dt = time.perf_counter() - t_get0
            if get_dt > 2.0:
                logger.warning(
                    "LLM cache Redis GET took %.1fs — check Redis and REDIS_SOCKET_TIMEOUT",
                    get_dt,
                )
            if cached is not None:
                logger.info("LLM cache HIT — skipping HTTP call")
                text, cached_usage = cached
                return (text, {**cached_usage, "cached": True})

        if _litellm_enabled():
            eff_api_key = (anthropic_api_key or api_key or "").strip() or None
            eff_base_url = (anthropic_base_url or base_url or "").strip() or None
            litellm_kwargs = {k: v for k, v in kwargs.items() if k not in (
                "llm_route", "anthropic_api_key", "anthropic_base_url",
            )}
            max_tokens = litellm_kwargs.pop("max_tokens", None)
            text, usage = _ask_litellm(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=eff_api_key,
                base_url=eff_base_url,
                **litellm_kwargs,
            )
            _accumulate_thread_usage(usage)
            if key is not None and self._cache is not None:
                self._cache.set(key, text, usage)
            return (text, usage)

        if _use_anthropic_backend(model, llm_route):
            text, usage = _ask_anthropic(
                messages=messages,
                model=model,
                temperature=temperature,
                anthropic_api_key=anthropic_api_key,
                anthropic_base_url=anthropic_base_url,
                **kwargs,
            )
            _accumulate_thread_usage(usage)
            if key is not None and self._cache is not None:
                self._cache.set(key, text, usage)
            return (text, usage)

        kwargs.pop("llm_route", None)
        _size = estimate_chat_request_size(messages)
        log_request_size(model, _size)
        maybe_warn_context_limit(model, _size)
        resolved_base_url = (base_url or os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL) or "").strip()
        if not resolved_base_url:
            resolved_base_url = OLLAMA_BASE_URL

        eff_base_url_for_pool = base_url or os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL)
        eff_api_key_for_pool = api_key or os.getenv("OPENAI_API_KEY", "ollama")
        client = self._pool.get(eff_base_url_for_pool, eff_api_key_for_pool)

        create_kwargs = merge_openai_compat_max_tokens(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                **kwargs,
            },
            base_url=resolved_base_url,
        )

        # Cap reasoning tokens for local thinking models (prevents 25-min runaway reasoning).
        _reasoning_budget = _local_llm_reasoning_budget(model, resolved_base_url)
        if _reasoning_budget is not None and "extra_body" not in create_kwargs:
            create_kwargs["extra_body"] = {"thinking_budget_tokens": _reasoning_budget}
            logger.info(
                "local reasoning budget cap: thinking_budget_tokens=%d model=%r",
                _reasoning_budget,
                model,
            )

        _req_timeout = _local_llm_request_timeout_sec(resolved_base_url)
        if _req_timeout is not None and "timeout" not in create_kwargs:
            create_kwargs["timeout"] = _req_timeout
            logger.info(
                "local LLM request timeout set: %.0fs model=%r",
                _req_timeout,
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
        if _local_llm_serialize_http_enabled(resolved_base_url):
            tmo = _local_llm_serialize_lock_acquire_timeout_sec()
            if tmo is None:
                with _LOCAL_LLM_HTTP_LOCK:
                    response = _chat_create()
            else:
                acquired = _LOCAL_LLM_HTTP_LOCK.acquire(timeout=tmo)
                if not acquired:
                    raise RuntimeError(
                        f"SWARM_LOCAL_LLM_SERIALIZE: could not acquire HTTP lock within {tmo}s "
                        "(another thread is inside chat.completions to the local LLM). "
                        "Disable SERIALIZE or set SWARM_LLM_SERIALIZE_ACQUIRE_TIMEOUT_SEC."
                    )
                try:
                    response = _chat_create()
                finally:
                    _LOCAL_LLM_HTTP_LOCK.release()
        else:
            response = _chat_create()

        if not response.choices:
            raise ValueError(f"LLM returned empty choices list (model={model})")
        text = response.choices[0].message.content or ""
        usage_obj = response.usage
        usage: dict[str, Any] = {
            "input_tokens": (getattr(usage_obj, "prompt_tokens", None) or 0),
            "output_tokens": (getattr(usage_obj, "completion_tokens", None) or 0),
            "model": model,
            "cached": False,
        }
        _accumulate_thread_usage(usage)
        if key is not None and self._cache is not None:
            self._cache.set(key, text, usage)
        return (text, usage)


# ---------------------------------------------------------------------------
# Module-level singleton router (backward compat)
# ---------------------------------------------------------------------------

def _get_default_router() -> "LLMRouter":
    """Return (or create) the process-level default :class:`LLMRouter`."""
    from backend.App.integrations.infrastructure.llm.cache import (
        cache_enabled,
        _get_default_cache,
    )
    from backend.App.integrations.infrastructure.llm.openai_client_pool import _default_pool

    cache = _get_default_cache() if cache_enabled() else None
    return LLMRouter(cache=cache, client_pool=_default_pool)
