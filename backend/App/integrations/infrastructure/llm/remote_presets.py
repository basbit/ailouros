from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def uses_anthropic_sdk(provider: str) -> bool:
    return (provider or "").strip().lower() == "anthropic"


OPENAI_COMPAT_PUBLIC_BASE_URLS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
}


def default_openai_compat_base_url(provider: str) -> str:
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
    provider_normalized = (provider or "").strip().lower()
    user_base_url_stripped = (user_base_url or "").strip()
    default = default_openai_compat_base_url(provider_normalized)

    if not user_base_url_stripped:
        if default:
            return default
        if provider_normalized == "ollama_cloud":
            return ""
        return _openai_env_fallback_base_url()

    inferred = _infer_openai_compat_vendor_from_host(_url_hostname(user_base_url_stripped))
    if inferred is not None and inferred != provider_normalized:
        if default:
            logger.info(
                "Ignoring remote_api.base_url=%r (host looks like provider=%s) "
                "for provider=%s; using default base URL.",
                user_base_url_stripped,
                inferred,
                provider_normalized,
            )
            return default
        return user_base_url_stripped

    if provider_normalized == "gemini" and default:
        url_lower = user_base_url_stripped.lower()
        if "generativelanguage.googleapis.com" in url_lower and "/v1beta/openai" not in url_lower:
            logger.info(
                "Gemini remote_api.base_url=%r is not the OpenAI-compatible endpoint; "
                "use .../v1beta/openai/ — using default.",
                user_base_url_stripped,
            )
            return default

    return user_base_url_stripped
