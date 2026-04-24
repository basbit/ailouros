from __future__ import annotations

import os
import time
from urllib.parse import urlparse
from typing import Any, Optional

import httpx
from fastapi.responses import JSONResponse

from backend.App.integrations.infrastructure.llm.remote_presets import (
    resolve_openai_compat_base_url,
    uses_anthropic_sdk,
)
from backend.App.integrations.infrastructure.llm.config import LMSTUDIO_BASE_URL, OLLAMA_BASE_URL

_MODELS_CACHE_TTL = float(os.getenv("SWARM_MODELS_CACHE_TTL_SEC", "60"))
_models_cache: dict[str, tuple[float, JSONResponse]] = {}

REMOTE_OPENAI_COMPAT_MODEL_PROVIDERS = frozenset(
    {
        "openai_compatible",
        "gemini",
        "groq",
        "cerebras",
        "openrouter",
        "deepseek",
        "ollama_cloud",
    }
)

_OPENAI_TEXT_MODEL_ALLOW_PREFIXES = (
    "gpt-",
    "gpt_",
    "gpt-oss",
    "codex",
    "o1",
    "o3",
    "o4",
    "ft:gpt-",
    "ft:gpt_",
    "ft:gpt-oss",
    "ft:codex",
    "ft:o1",
    "ft:o3",
    "ft:o4",
)
_OPENAI_TEXT_MODEL_BLOCK_SUBSTRINGS = (
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "embedding",
    "moderation",
    "image",
    "whisper",
    "dall",
    "sora",
    "chatgpt",
    "search-preview",
    "computer-use",
    "deep-research",
)
_GEMINI_TEXT_MODEL_ALLOW_PREFIXES = (
    "gemini-",
    "learnlm-",
)
_GEMINI_TEXT_MODEL_BLOCK_SUBSTRINGS = (
    "image",
    "audio",
    "native-audio",
    "tts",
    "live",
)


def _format_capabilities_display(caps: Any) -> str:
    if caps is None:
        return ""
    if isinstance(caps, list):
        return ", ".join(str(x) for x in caps if x is not None and str(x) != "")
    if isinstance(caps, dict):
        return ", ".join(str(k) for k, v in caps.items() if v)
    return str(caps)


def _format_context_window(ctx: Any) -> str:
    try:
        n = int(ctx)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{round(n / 1_000_000)}M ctx"
    if n >= 1_000:
        return f"{round(n / 1_000)}k ctx"
    return f"{n} ctx"


def _openai_model_row(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    mid = (item.get("id") or "").strip()
    if not mid:
        return None
    caps = item.get("capabilities")
    if caps is None:
        caps = item.get("Capabilities")
    cap_str = _format_capabilities_display(caps)
    ctx_str = _format_context_window(item.get("context_window") or item.get("context_length"))
    hint = " · ".join(x for x in [ctx_str, cap_str] if x)
    label = f"{mid} ({hint})" if hint else mid
    return {"id": mid, "label": label, "context_window": item.get("context_window") or item.get("context_length")}


_ANTHROPIC_KNOWN_MODELS: list[dict[str, Any]] = [
    {"id": "claude-opus-4-6", "context_window": 200_000},
    {"id": "claude-sonnet-4-6", "context_window": 200_000},
    {"id": "claude-haiku-4-5", "context_window": 200_000},
    {"id": "claude-3-5-sonnet-latest", "context_window": 200_000},
    {"id": "claude-3-5-haiku-latest", "context_window": 200_000},
    {"id": "claude-3-opus-latest", "context_window": 200_000},
    {"id": "claude-3-haiku-20240307", "context_window": 200_000},
]


def _anthropic_models_list() -> dict[str, Any]:
    rows = []
    for m in _ANTHROPIC_KNOWN_MODELS:
        ctx = m.get("context_window")
        ctx_str = _format_context_window(ctx)
        label = f"{m['id']} ({ctx_str})" if ctx_str else m["id"]
        rows.append({"id": m["id"], "label": label, "context_window": ctx})
    return {"ok": True, "models": rows, "source": "built-in"}


def normalize_openai_v1_models_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        row = _openai_model_row(raw)
        if row:
            out.append(row)
    return out


def _is_openai_first_party_base_url(base_url: str) -> bool:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return (
        host == "api.openai.com"
        or host.endswith(".api.openai.com")
        or "openai.azure.com" in host
    )


def _is_openai_text_generation_model(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if not mid.startswith(_OPENAI_TEXT_MODEL_ALLOW_PREFIXES):
        return False
    if any(part in mid for part in _OPENAI_TEXT_MODEL_BLOCK_SUBSTRINGS):
        return False
    return True


def _filter_openai_models_for_orchestrator(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        row for row in models
        if _is_openai_text_generation_model(str(row.get("id") or ""))
    ]
    return filtered or models


def _is_gemini_first_party_base_url(base_url: str) -> bool:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if not host:
        return False
    return (
        host == "generativelanguage.googleapis.com"
        or host.endswith(".generativelanguage.googleapis.com")
    )


def _gemini_native_models_url(base_url: str) -> str:
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme and parsed.netloc and _is_gemini_first_party_base_url(base_url):
        return f"{parsed.scheme}://{parsed.netloc}/v1beta/models"
    return "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_supported_generation_methods(item: dict[str, Any]) -> list[str]:
    raw_methods = item.get("supportedGenerationMethods") or item.get("supported_actions") or []
    if not isinstance(raw_methods, list):
        return []
    return [str(method).strip() for method in raw_methods if str(method).strip()]


def _gemini_model_id(item: dict[str, Any]) -> str:
    mid = str(item.get("baseModelId") or "").strip()
    if mid:
        return mid
    name = str(item.get("name") or "").strip()
    if name.startswith("models/"):
        return name.split("/", 1)[1].strip()
    return name


def _gemini_model_row(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    mid = _gemini_model_id(item)
    if not mid:
        return None
    methods = _gemini_supported_generation_methods(item)
    ctx = item.get("inputTokenLimit") or item.get("input_token_limit")
    ctx_str = _format_context_window(ctx)
    cap_str = _format_capabilities_display(methods)
    hint = " · ".join(x for x in [ctx_str, cap_str] if x)
    label = f"{mid} ({hint})" if hint else mid
    return {
        "id": mid,
        "label": label,
        "context_window": ctx,
        "supported_generation_methods": methods,
    }


def normalize_gemini_native_models_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in payload.get("models") or []:
        if not isinstance(raw, dict):
            continue
        row = _gemini_model_row(raw)
        if row:
            out.append(row)
    return out


def _is_gemini_text_generation_model(model_id: str) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if not mid.startswith(_GEMINI_TEXT_MODEL_ALLOW_PREFIXES):
        return False
    if any(part in mid for part in _GEMINI_TEXT_MODEL_BLOCK_SUBSTRINGS):
        return False
    return True


def _filter_gemini_models_for_orchestrator(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    generation_capable = [
        row for row in models
        if "generateContent" in (row.get("supported_generation_methods") or [])
    ]
    filtered = [
        row for row in generation_capable
        if _is_gemini_text_generation_model(str(row.get("id") or ""))
    ]
    keep = filtered or generation_capable or models
    return [
        {
            "id": str(row.get("id") or ""),
            "label": str(row.get("label") or row.get("id") or ""),
            "context_window": row.get("context_window"),
        }
        for row in keep
        if str(row.get("id") or "").strip()
    ]


def _gemini_native_models_dict(
    *,
    base_url: str,
    api_key: Optional[str] = None,
) -> dict[str, Any]:
    url = _gemini_native_models_url(base_url)
    params: dict[str, Any] = {"pageSize": 1000}
    if api_key:
        params["key"] = api_key
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            models = _filter_gemini_models_for_orchestrator(
                normalize_gemini_native_models_payload(r.json())
            )
    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        return {
            "ok": False,
            "error": err,
            "models": [],
            "source": url,
        }
    return {
        "ok": True,
        "models": models,
        "source": url,
    }


def normalize_ollama_tags_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in payload.get("models") or []:
        if not isinstance(raw, dict):
            continue
        mid = (raw.get("name") or raw.get("model") or "").strip()
        if not mid:
            continue
        caps = raw.get("capabilities")
        if caps is None:
            caps = raw.get("Capabilities")
        cap_str = _format_capabilities_display(caps)
        ctx = raw.get("context_length") or raw.get("context_window")
        if ctx is None:
            model_info = raw.get("model_info") or {}
            ctx = model_info.get("llama.context_length") or model_info.get("context_length")
        ctx_str = _format_context_window(ctx)
        hint = " · ".join(x for x in [ctx_str, cap_str] if x)
        label = f"{mid} ({hint})" if hint else mid
        out.append({"id": mid, "label": label, "context_window": ctx})
    return out


def _cached_response(cache_key: str) -> Optional[JSONResponse]:
    entry = _models_cache.get(cache_key)
    if entry and (time.monotonic() - entry[0]) < _MODELS_CACHE_TTL:
        return entry[1]
    return None


def _store_response(cache_key: str, resp: JSONResponse) -> JSONResponse:
    _models_cache[cache_key] = (time.monotonic(), resp)
    return resp


def ollama_models_proxy_response() -> JSONResponse:
    base = os.getenv("OPENAI_BASE_URL", OLLAMA_BASE_URL).rstrip("/")
    v1_url = f"{base}/models"
    cache_key = f"ollama:{v1_url}"

    cached = _cached_response(cache_key)
    if cached is not None:
        return cached

    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(v1_url)
            r.raise_for_status()
            models = normalize_openai_v1_models_payload(r.json())
    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": err, "models": [], "source": v1_url},
        )

    return _store_response(
        cache_key,
        JSONResponse({"ok": True, "models": models, "source": v1_url, "via": "v1/models"}),
    )


def lmstudio_models_proxy_response() -> JSONResponse:
    base = os.getenv("LMSTUDIO_BASE_URL", LMSTUDIO_BASE_URL).rstrip("/")
    url = f"{base}/models"
    cache_key = f"lmstudio:{url}"

    cached = _cached_response(cache_key)
    if cached is not None:
        return cached

    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "models": [], "source": url},
        )
    models = normalize_openai_v1_models_payload(payload)
    return _store_response(
        cache_key,
        JSONResponse({"ok": True, "models": models, "source": url}),
    )


def remote_openai_compatible_models_dict(
    *,
    provider: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict[str, Any]:
    prov = (provider or "").strip().lower()
    if prov not in REMOTE_OPENAI_COMPAT_MODEL_PROVIDERS and not uses_anthropic_sdk(prov):
        return {
            "ok": False,
            "error": f"provider {prov!r}: model list via API is not supported.",
            "models": [],
            "source": "",
        }
    if uses_anthropic_sdk(prov):
        return _anthropic_models_list()

    u_base = (base_url or "").strip() or None
    key = (api_key or "").strip() or None
    resolved = resolve_openai_compat_base_url(prov, u_base)
    if not resolved:
        return {
            "ok": False,
            "error": "base_url is required (mandatory for Ollama Cloud).",
            "models": [],
            "source": "",
        }
    if prov == "gemini" and _is_gemini_first_party_base_url(resolved):
        return _gemini_native_models_dict(base_url=resolved, api_key=key)
    url = f"{resolved.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            models = normalize_openai_v1_models_payload(r.json())
            if prov == "openai_compatible" and _is_openai_first_party_base_url(resolved):
                models = _filter_openai_models_for_orchestrator(models)
    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        return {
            "ok": False,
            "error": err,
            "models": [],
            "source": url,
        }
    return {
        "ok": True,
        "models": models,
        "source": url,
    }


def remote_openai_compatible_models_response(
    *,
    provider: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> JSONResponse:
    payload = remote_openai_compatible_models_dict(
        provider=provider, base_url=base_url, api_key=api_key
    )
    return JSONResponse(content=payload)
