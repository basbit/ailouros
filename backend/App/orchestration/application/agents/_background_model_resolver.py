from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_LEGACY_ANTHROPIC_BACKGROUND_MODEL = "claude-haiku-4-5"
_MODEL_IDS_CACHE_TTL_SEC = float(
    os.getenv("SWARM_BACKGROUND_AGENT_MODEL_CACHE_TTL_SEC", "60")
)
_PROVIDER_MODEL_IDS_CACHE: dict[tuple[str, str, str], tuple[float, list[str]]] = {}
_LOCAL_MODEL_IDS_CACHE: dict[str, tuple[float, list[str]]] = {}

PROVIDER_FALLBACK_MODELS: dict[str, str] = {
    "anthropic": _LEGACY_ANTHROPIC_BACKGROUND_MODEL,
    "gemini": "gemini-2.0-flash",
    "openai_compatible": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}

PROVIDER_MODEL_PREFERENCES: dict[str, tuple[str, ...]] = {
    "anthropic": (
        "claude-haiku-4-5",
        "claude-3-5-haiku-latest",
        "claude-3-5-sonnet-latest",
    ),
    "gemini": (
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ),
    "openai_compatible": (
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4o",
        "gpt-4.1",
    ),
    "deepseek": (
        "deepseek-chat",
        "deepseek-reasoner",
    ),
}


def effective_cloud_provider(
    environment: str,
    remote_provider: str,
    model_for_infer: str,
) -> str:
    env_key = (environment or "").strip().lower()
    provider = (remote_provider or "").strip().lower()
    if env_key == "anthropic":
        return provider or "anthropic"
    if provider:
        return provider
    model = (model_for_infer or "").strip().lower()
    if model.startswith("claude") or model.startswith("anthropic/"):
        return "anthropic"
    if model.startswith("gemini") or model.startswith("learnlm"):
        return "gemini"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt", "openai/", "codex")):
        return "openai_compatible"
    return "anthropic"


def pick_preferred_model(provider: str, model_ids: list[str]) -> str:
    if not model_ids:
        return ""
    by_lower = {mid.lower(): mid for mid in model_ids if mid}
    for preferred in PROVIDER_MODEL_PREFERENCES.get(provider, ()):
        hit = by_lower.get(preferred.lower())
        if hit:
            return hit
    if provider == "gemini":
        for mid in model_ids:
            lowered = mid.lower()
            if lowered.startswith(("gemini-", "learnlm-")) and "flash" in lowered:
                return mid
    if provider == "anthropic":
        for mid in model_ids:
            lowered = mid.lower()
            if lowered.startswith("claude") and "haiku" in lowered:
                return mid
    return model_ids[0]


def is_obviously_incompatible_model(provider: str, model: str) -> bool:
    lowered = (model or "").strip().lower()
    if not lowered:
        return False
    if provider == "anthropic":
        return not (lowered.startswith("claude") or lowered.startswith("anthropic/"))
    if provider == "gemini":
        return not (lowered.startswith("gemini") or lowered.startswith("learnlm"))
    if provider in {"openai_compatible", "groq", "cerebras", "deepseek"}:
        return lowered.startswith(("claude", "anthropic/", "gemini", "learnlm"))
    return False


def is_gemini_first_party_base_url(base_url: str) -> bool:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return (
        host == "generativelanguage.googleapis.com"
        or host.endswith(".generativelanguage.googleapis.com")
    )


def gemini_native_models_url(base_url: str) -> str:
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme and parsed.netloc and is_gemini_first_party_base_url(base_url):
        return f"{parsed.scheme}://{parsed.netloc}/v1beta/models"
    return "https://generativelanguage.googleapis.com/v1beta/models"


def openai_model_ids(payload: dict[str, Any]) -> list[str]:
    return [
        str(item.get("id") or "").strip()
        for item in (payload.get("data") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def gemini_model_ids(payload: dict[str, Any]) -> list[str]:
    model_ids: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        supported = item.get("supportedGenerationMethods") or []
        if not isinstance(supported, list) or "generateContent" not in supported:
            continue
        mid = str(item.get("baseModelId") or "").strip()
        if not mid:
            name = str(item.get("name") or "").strip()
            if name.startswith("models/"):
                mid = name.split("/", 1)[1].strip()
            else:
                mid = name
        if mid:
            model_ids.append(mid)
    return model_ids


def fetch_local_model_ids(environment: str) -> list[str]:
    import httpx

    from backend.App.integrations.infrastructure.llm.config import (
        LMSTUDIO_BASE_URL,
        OLLAMA_BASE_URL,
    )

    env_key = (environment or "").strip().lower()
    cached = _LOCAL_MODEL_IDS_CACHE.get(env_key)
    now = time.monotonic()
    if cached and (now - cached[0]) < _MODEL_IDS_CACHE_TTL_SEC:
        return list(cached[1])

    try:
        if env_key in {"lmstudio", "lm_studio"}:
            base_url = os.getenv("LMSTUDIO_BASE_URL", LMSTUDIO_BASE_URL).rstrip("/")
            api_key = os.getenv("LMSTUDIO_API_KEY", "lm-studio")
        else:
            base_url = os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL).rstrip("/")
            api_key = os.getenv("OPENAI_API_KEY", "ollama")
        with httpx.Client(timeout=8.0) as client:
            response = client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []
    model_ids = openai_model_ids(payload)
    _LOCAL_MODEL_IDS_CACHE[env_key] = (now, model_ids)
    return model_ids


def default_local_background_model(environment: str) -> str:
    env_key = (environment or "").strip().lower()
    candidates: list[str]
    if env_key in {"lmstudio", "lm_studio"}:
        candidates = [
            os.getenv("SWARM_LMSTUDIO_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_LMSTUDIO_MODEL_PLANNING", "").strip(),
            os.getenv("SWARM_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_MODEL", "").strip(),
            os.getenv("SWARM_MODEL_PLANNING", "").strip(),
        ]
    else:
        candidates = [
            os.getenv("SWARM_MODEL_BUILD", "").strip(),
            os.getenv("SWARM_MODEL", "").strip(),
            os.getenv("SWARM_MODEL_PLANNING", "").strip(),
        ]
    for candidate in candidates:
        if candidate:
            return candidate
    local_models = fetch_local_model_ids(environment)
    return local_models[0] if local_models else ""


def fetch_provider_model_ids(
    provider: str,
    *,
    api_key: str,
    base_url: str,
) -> list[str]:
    import httpx

    from backend.App.integrations.infrastructure.llm.remote_presets import (
        resolve_openai_compat_base_url,
        uses_anthropic_sdk,
    )

    provider_key = (provider or "").strip().lower()
    cache_key = (
        provider_key,
        (base_url or "").strip(),
        (api_key or "").strip(),
    )
    cached = _PROVIDER_MODEL_IDS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and (now - cached[0]) < _MODEL_IDS_CACHE_TTL_SEC:
        return list(cached[1])

    if uses_anthropic_sdk(provider_key):
        model_ids = [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
        ]
        _PROVIDER_MODEL_IDS_CACHE[cache_key] = (now, model_ids)
        return model_ids

    resolved_base_url = resolve_openai_compat_base_url(
        provider_key,
        (base_url or "").strip() or None,
    )
    if not resolved_base_url:
        return []
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=12.0) as client:
            if (
                provider_key == "gemini"
                and is_gemini_first_party_base_url(resolved_base_url)
            ):
                params: dict[str, Any] = {"pageSize": 1000}
                if api_key:
                    params["key"] = api_key
                response = client.get(
                    gemini_native_models_url(resolved_base_url),
                    params=params,
                )
                response.raise_for_status()
                model_ids = gemini_model_ids(response.json())
            else:
                response = client.get(
                    f"{resolved_base_url.rstrip('/')}/models",
                    headers=headers,
                )
                response.raise_for_status()
                model_ids = openai_model_ids(response.json())
    except Exception:
        return []
    _PROVIDER_MODEL_IDS_CACHE[cache_key] = (now, model_ids)
    return model_ids


def resolve_background_model(
    *,
    environment: str,
    model: str,
    remote_provider: str,
    remote_api_key: str,
    remote_base_url: str,
    provider_model_fetcher: Any = None,
) -> str:
    requested = (model or "").strip()
    env_key = (environment or "").strip().lower()
    if env_key not in {"cloud", "anthropic"}:
        return requested or default_local_background_model(environment)

    provider = effective_cloud_provider(environment, remote_provider, requested)
    fetcher = provider_model_fetcher or fetch_provider_model_ids
    available_model_ids = fetcher(
        provider,
        api_key=(remote_api_key or "").strip(),
        base_url=(remote_base_url or "").strip(),
    )
    if available_model_ids:
        requested_lower = requested.lower()
        for available in available_model_ids:
            if available.lower() == requested_lower and requested:
                return available
        replacement = pick_preferred_model(provider, available_model_ids)
        if replacement:
            if requested and replacement != requested:
                logger.info(
                    "BackgroundAgent: provider=%s does not advertise model=%s; using model=%s",
                    provider, requested, replacement,
                )
            return replacement

    if requested and not is_obviously_incompatible_model(provider, requested):
        return requested

    fallback = PROVIDER_FALLBACK_MODELS.get(provider, "")
    if fallback:
        if requested and requested != fallback:
            logger.info(
                "BackgroundAgent: provider=%s is incompatible with model=%s; "
                "falling back to model=%s",
                provider, requested, fallback,
            )
        return fallback
    return requested


__all__ = (
    "PROVIDER_FALLBACK_MODELS",
    "PROVIDER_MODEL_PREFERENCES",
    "effective_cloud_provider",
    "pick_preferred_model",
    "is_obviously_incompatible_model",
    "is_gemini_first_party_base_url",
    "gemini_native_models_url",
    "openai_model_ids",
    "gemini_model_ids",
    "fetch_local_model_ids",
    "default_local_background_model",
    "fetch_provider_model_ids",
    "resolve_background_model",
)
