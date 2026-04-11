"""Пресеты удалённых API: OpenAI-compatible endpoint + дефолтный base URL.

Anthropic — отдельный SDK (provider == anthropic).
Остальные — через OpenAI Python client (chat.completions).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def uses_anthropic_sdk(provider: str) -> bool:
    return (provider or "").strip().lower() == "anthropic"


# Известные публичные base URL (OpenAI-compatible). Дублируется в UI: REMOTE_API_BASE_PRESETS.
# Доки: https://ai.google.dev/gemini-api/docs/openai и сайты провайдеров.
OPENAI_COMPAT_PUBLIC_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def default_openai_compat_base_url(provider: str) -> str:
    """Пустая строка = вызывающий код подставит OPENAI_BASE_URL или api.openai.com."""
    p = (provider or "").strip().lower()
    if p == "openai_compatible":
        return (
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1"
        ).strip()
    if p == "ollama_cloud":
        return (os.getenv("OLLAMA_CLOUD_OPENAI_BASE_URL", "") or "").strip()
    return (OPENAI_COMPAT_PUBLIC_BASE_URLS.get(p) or "").strip()


def _openai_env_fallback_base_url() -> str:
    return (
        os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1"
    ).strip()


def _url_hostname(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except Exception as exc:
        logger.debug("Could not parse URL hostname from %r: %s", url, exc)
        return ""


def _infer_openai_compat_vendor_from_host(host: str) -> Optional[str]:
    """Однозначный хост публичного API провайдера → id провайдера. Иначе None (прокси, Azure и т.д.)."""
    if not host:
        return None
    if "generativelanguage.googleapis.com" in host:
        return "gemini"
    if host == "api.groq.com":
        return "groq"
    if "cerebras.ai" in host:
        return "cerebras"
    if "openrouter.ai" in host:
        return "openrouter"
    if "deepseek.com" in host:
        return "deepseek"
    if host == "api.openai.com" or host.endswith(".api.openai.com"):
        return "openai_compatible"
    if "openai.azure.com" in host:
        return "openai_compatible"
    return None


def resolve_openai_compat_base_url(
    provider: str,
    user_base_url: Optional[str] = None,
) -> str:
    """Итоговый base_url для OpenAI-совместимого клиента.

    Если в UI остался base_url от **другого** облачного провайдера (типично после смены
    provider в селекте), подставляем дефолт для выбранного provider — иначе 404 на чужом хосте.
    """
    prov = (provider or "").strip().lower()
    u = (user_base_url or "").strip()
    default = default_openai_compat_base_url(prov)

    if not u:
        if default:
            return default
        if prov == "ollama_cloud":
            return ""
        return _openai_env_fallback_base_url()

    inferred = _infer_openai_compat_vendor_from_host(_url_hostname(u))
    if inferred is not None and inferred != prov:
        if default:
            logger.info(
                "Ignoring remote_api.base_url=%r (host looks like provider=%s) "
                "for provider=%s; using default base URL.",
                u,
                inferred,
                prov,
            )
            return default
        return u

    # Gemini OpenAI-compat lives under /v1beta/openai/, not /v1/.
    # Wrong path → POST .../v1/chat/completions → 404 (native REST path, not OpenAI shim).
    if prov == "gemini" and default:
        u_low = u.lower()
        if "generativelanguage.googleapis.com" in u_low and "/v1beta/openai" not in u_low:
            logger.info(
                "Gemini remote_api.base_url=%r is not the OpenAI-compatible endpoint; "
                "use .../v1beta/openai/ — using default.",
                u,
            )
            return default

    return u
