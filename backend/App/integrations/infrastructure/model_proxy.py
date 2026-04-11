"""Парсинг списков моделей и прокси к Ollama / LM Studio / remote OpenAI-compatible API."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from fastapi.responses import JSONResponse

from backend.App.integrations.infrastructure.llm.remote_presets import (
    resolve_openai_compat_base_url,
    uses_anthropic_sdk,
)
from backend.App.integrations.infrastructure.llm.config import LMSTUDIO_BASE_URL, OLLAMA_BASE_URL

# ---------------------------------------------------------------------------
# Simple TTL cache for model list requests.
# Each key → (timestamp, JSONResponse).  Default TTL: 60 s (env override).
# ---------------------------------------------------------------------------

_MODELS_CACHE_TTL = float(os.getenv("SWARM_MODELS_CACHE_TTL_SEC", "60"))
_models_cache: dict[str, tuple[float, JSONResponse]] = {}

# Провайдеры, для которых есть GET {base}/models в стиле OpenAI (не Anthropic SDK).
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


def _format_capabilities_display(caps: Any) -> str:
    if caps is None:
        return ""
    if isinstance(caps, list):
        return ", ".join(str(x) for x in caps if x is not None and str(x) != "")
    if isinstance(caps, dict):
        return ", ".join(str(k) for k, v in caps.items() if v)
    return str(caps)


def _fmt_ctx(ctx: Any) -> str:
    """Format context_window as human-readable string: 131072 → '128k'."""
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
    ctx_str = _fmt_ctx(item.get("context_window") or item.get("context_length"))
    hint = " · ".join(x for x in [ctx_str, cap_str] if x)
    label = f"{mid} ({hint})" if hint else mid
    return {"id": mid, "label": label, "context_window": item.get("context_window") or item.get("context_length")}


# Known Anthropic models with context window sizes (no public /models endpoint).
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
        ctx_str = _fmt_ctx(ctx)
        label = f"{m['id']} ({ctx_str})" if ctx_str else m["id"]
        rows.append({"id": m["id"], "label": label, "context_window": ctx})
    return {"ok": True, "models": rows, "source": "built-in"}


def normalize_openai_v1_models_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Parse OpenAI-style GET /v1/models (Ollama, LM Studio, …)."""
    out: list[dict[str, str]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        row = _openai_model_row(raw)
        if row:
            out.append(row)
    return out


def normalize_ollama_tags_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse native Ollama GET /api/tags."""
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
        # Ollama tags may expose context_length directly or inside model_info
        ctx = raw.get("context_length") or raw.get("context_window")
        if ctx is None:
            model_info = raw.get("model_info") or {}
            ctx = model_info.get("llama.context_length") or model_info.get("context_length")
        ctx_str = _fmt_ctx(ctx)
        hint = " · ".join(x for x in [ctx_str, cap_str] if x)
        label = f"{mid} ({hint})" if hint else mid
        out.append({"id": mid, "label": label, "context_window": ctx})
    return out


def _cached_response(cache_key: str) -> Optional[JSONResponse]:
    """Return a cached JSONResponse if still within TTL, else None."""
    entry = _models_cache.get(cache_key)
    if entry and (time.monotonic() - entry[0]) < _MODELS_CACHE_TTL:
        return entry[1]
    return None


def _store_response(cache_key: str, resp: JSONResponse) -> JSONResponse:
    _models_cache[cache_key] = (time.monotonic(), resp)
    return resp


def ollama_models_proxy_response() -> JSONResponse:
    """GET {OPENAI_BASE_URL}/models (OpenAI-совместимый эндпоинт Ollama).

    Results are cached for SWARM_MODELS_CACHE_TTL_SEC (default 60 s) to
    avoid a network round-trip on every UI poll.
    """
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
        # Don't cache error responses
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": err, "models": [], "source": v1_url},
        )

    return _store_response(
        cache_key,
        JSONResponse({"ok": True, "models": models, "source": v1_url, "via": "v1/models"}),
    )


def lmstudio_models_proxy_response() -> JSONResponse:
    """GET {LMSTUDIO_BASE_URL}/models (default localhost:1234/v1/models).

    Results are cached for SWARM_MODELS_CACHE_TTL_SEC (default 60 s).
    """
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
    """GET {resolved_base}/models для OpenAI-compatible remote API. Тело ответа для UI."""
    prov = (provider or "").strip().lower()
    if prov not in REMOTE_OPENAI_COMPAT_MODEL_PROVIDERS and not uses_anthropic_sdk(prov):
        return {
            "ok": False,
            "error": f"provider {prov!r}: список моделей через API не поддерживается.",
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
            "error": "Укажите base URL (для Ollama Cloud он обязателен).",
            "models": [],
            "source": "",
        }
    url = f"{resolved.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            models = normalize_openai_v1_models_payload(r.json())
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
    """Всегда HTTP 200 + {ok, models, error?, source?} — удобно для fetch().json()."""
    payload = remote_openai_compatible_models_dict(
        provider=provider, base_url=base_url, api_key=api_key
    )
    return JSONResponse(content=payload)
