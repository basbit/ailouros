"""Provider-specific LLM backends.

Extracted from client.py: _litellm_enabled, _is_cloud_model, _use_anthropic_backend,
_build_anthropic_client, _ask_anthropic, _ask_litellm.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from anthropic import Anthropic

from backend.App.integrations.infrastructure.llm.prompt_size import (
    estimate_chat_request_size,
    log_request_size,
    maybe_warn_context_limit,
)

logger = logging.getLogger(__name__)


def _litellm_enabled() -> bool:
    """Включить через SWARM_USE_LITELLM=1. Требует: pip install litellm."""
    return os.getenv("SWARM_USE_LITELLM", "").strip().lower() in ("1", "true", "yes")


_CLOUD_MODEL_PREFIXES = tuple(
    p.strip() for p in os.getenv(
        "SWARM_CLOUD_MODEL_PREFIXES", "claude,anthropic/"
    ).split(",") if p.strip()
)


def _is_cloud_model(model: str) -> bool:
    return any(model.startswith(p) for p in _CLOUD_MODEL_PREFIXES)


def _use_anthropic_backend(model: str, llm_route: Optional[str]) -> bool:
    """Какой HTTP-бэкенд: anthropic SDK vs OpenAI-совместимый клиент."""
    r = (llm_route or "").strip().lower()
    if r == "openai":
        return False
    if r == "anthropic":
        return True
    return _is_cloud_model(model)


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
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    *,
    anthropic_api_key: Optional[str] = None,
    anthropic_base_url: Optional[str] = None,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> tuple[str, dict]:
    # OpenAI-local kwargs не должны утекать в Anthropic API
    kwargs.pop("base_url", None)
    kwargs.pop("api_key", None)
    kwargs.pop("llm_route", None)
    kwargs.pop("anthropic_api_key", None)
    kwargs.pop("anthropic_base_url", None)
    _size = estimate_chat_request_size(messages)
    log_request_size(model, _size)
    maybe_warn_context_limit(model, _size)
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    system_prompt = "\n\n".join(system_parts).strip()
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        chat_messages.append(
            {
                "role": role,
                "content": [{"type": "text", "text": m["content"]}],
            }
        )

    resolved_max_tokens = (
        max_tokens if max_tokens is not None
        else int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))
    )
    client = _build_anthropic_client(anthropic_api_key, anthropic_base_url)
    model_name = model.replace("anthropic/", "", 1)
    logger.info("Anthropic API messages.create: model=%r", model_name)
    response = client.messages.create(
        model=model_name,
        system=system_prompt,
        messages=chat_messages,
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
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    *,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, dict[str, Any]]:
    """litellm unified backend: supports 200+ providers via a single API.

    Models: use provider prefix — gemini/gemini-2.0-flash,
    anthropic/<model>, ollama/<model> etc.
    Without prefix litellm will try to detect the provider automatically.
    """
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError(
            "SWARM_USE_LITELLM=1 требует пакет 'litellm': pip install litellm"
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
