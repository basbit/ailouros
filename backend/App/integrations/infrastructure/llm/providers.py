from __future__ import annotations

import logging
import os
from typing import Any, Optional, cast

from anthropic import Anthropic

from backend.App.integrations.infrastructure.llm.prompt_size import (
    estimate_chat_request_size,
    log_request_size,
    maybe_warn_context_limit,
)
from backend.App.integrations.infrastructure.llm.prompt_cache import (
    apply_anthropic_cache_control,
)
from backend.App.shared.infrastructure.message_formatting import (
    to_anthropic_messages,
)
from backend.App.shared.infrastructure.model_routing import (  # noqa: F401
    is_cloud_model as _is_cloud_model,
    should_use_anthropic_backend as _use_anthropic_backend,
)

logger = logging.getLogger(__name__)


def _litellm_enabled() -> bool:
    return os.getenv("SWARM_USE_LITELLM", "").strip().lower() in ("1", "true", "yes")


def _build_anthropic_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Anthropic:
    key = (api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is required for cloud models")
    url_raw = base_url if base_url is not None else os.getenv("ANTHROPIC_BASE_URL")
    url = (url_raw or "").strip()
    if url:
        return Anthropic(api_key=key, base_url=url)
    return Anthropic(api_key=key)


def _ask_anthropic(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    *,
    anthropic_api_key: Optional[str] = None,
    anthropic_base_url: Optional[str] = None,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> tuple[str, dict]:
    kwargs.pop("base_url", None)
    kwargs.pop("api_key", None)
    kwargs.pop("llm_route", None)
    kwargs.pop("anthropic_api_key", None)
    kwargs.pop("anthropic_base_url", None)
    _size = estimate_chat_request_size(messages)
    log_request_size(model, _size)
    maybe_warn_context_limit(model, _size)
    system_prompt, chat_messages = to_anthropic_messages(messages)

    resolved_max_tokens = (
        max_tokens if max_tokens is not None
        else int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))
    )
    client = _build_anthropic_client(anthropic_api_key, anthropic_base_url)
    model_name = model.replace("anthropic/", "", 1)
    system_param, chat_messages = apply_anthropic_cache_control(system_prompt, chat_messages)
    logger.info("Anthropic API messages.create: model=%r", model_name)
    response = client.messages.create(
        model=model_name,
        system=cast(Any, system_param),
        messages=cast(Any, chat_messages),
        temperature=temperature,
        max_tokens=resolved_max_tokens,
        **kwargs,
    )
    parts = []
    for chunk in response.content:
        text = getattr(chunk, "text", "")
        if text:
            parts.append(text)
    text_out = "".join(parts).strip()
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        "model": model,
        "cached": False,
    }
    return text_out, usage


def _ask_litellm(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    *,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError(
            "SWARM_USE_LITELLM=1 requires the 'litellm' package: pip install litellm"
        ) from exc

    _size = estimate_chat_request_size(messages)
    log_request_size(model, _size)
    maybe_warn_context_limit(model, _size)

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        call_kwargs["max_tokens"] = max_tokens
    if api_key:
        call_kwargs["api_key"] = api_key
    if base_url:
        call_kwargs["base_url"] = base_url

    response = litellm.completion(**call_kwargs)
    if not response.choices:
        raise ValueError(f"LLM returned empty choices list (model={model})")
    text = (response.choices[0].message.content or "").strip()
    usage_obj = response.usage
    usage: dict[str, Any] = {
        "input_tokens": getattr(usage_obj, "prompt_tokens", None) or 0,
        "output_tokens": getattr(usage_obj, "completion_tokens", None) or 0,
        "model": model,
        "cached": False,
    }
    return text, usage
