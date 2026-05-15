from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.infrastructure.activity_recorder import record as record_activity
from backend.App.shared.infrastructure.app_config_load import load_app_config_json
from backend.App.shared.infrastructure.secrets_store import load_secret
from backend.App.shared.infrastructure.tracing import trace_span

try:
    import requests  # type: ignore[import-untyped]
except ImportError as _requests_import_error:
    requests = None  # type: ignore[assignment]
    _REQUESTS_IMPORT_ERROR: Optional[BaseException] = _requests_import_error
else:
    _REQUESTS_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

_COUNTS_FILE = Path.home() / ".swarm" / "web_search_counts.json"
_MAX_QUERY_PREVIEW = 200


def _require_requests() -> Any:
    if requests is None:
        raise RuntimeError(
            "'requests' package is required for web search providers. "
            f"Original import error: {_REQUESTS_IMPORT_ERROR!r}"
        )
    return requests


def _config() -> dict[str, Any]:
    return load_app_config_json("web_search.json")


def _providers_order() -> list[str]:
    raw = _config().get("providers_order") or []
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("web_search.json: providers_order must be a non-empty list")
    return [str(name).strip() for name in raw if str(name).strip()]


def _env_key_name(provider: str) -> str:
    env_keys = _config().get("env_keys") or {}
    if not isinstance(env_keys, dict):
        raise RuntimeError("web_search.json: env_keys must be an object")
    raw = env_keys.get(provider)
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(
            f"web_search.json: env_keys[{provider!r}] is missing or empty"
        )
    return raw.strip()


def _secret_name(provider: str) -> str:
    secret_names = _config().get("secret_names") or {}
    if isinstance(secret_names, dict):
        raw = secret_names.get(provider)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return f"web_search.{provider}"


def _free_tier_limit() -> int:
    raw = _config().get("free_tier_limit")
    if not isinstance(raw, int) or raw <= 0:
        raise RuntimeError("web_search.json: free_tier_limit must be a positive integer")
    return raw


def _request_timeout_sec() -> float:
    raw = _config().get("request_timeout_sec", 15)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    raise RuntimeError("web_search.json: request_timeout_sec must be a positive number")


def _invalid_key_ttl_sec() -> int:
    raw = _config().get("invalid_key_ttl_sec", 300)
    if isinstance(raw, int) and raw > 0:
        return raw
    raise RuntimeError("web_search.json: invalid_key_ttl_sec must be a positive integer")


_invalid_lock = threading.RLock()
_invalid_until: dict[str, float] = {}


def _mark_invalid(provider: str) -> None:
    until = time.monotonic() + _invalid_key_ttl_sec()
    with _invalid_lock:
        _invalid_until[provider] = until


def _is_invalid(provider: str) -> bool:
    with _invalid_lock:
        until = _invalid_until.get(provider)
        if until is None:
            return False
        if time.monotonic() >= until:
            _invalid_until.pop(provider, None)
            return False
        return True


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_counts() -> dict[str, dict[str, int]]:
    if not _COUNTS_FILE.is_file():
        return {}
    raw = _COUNTS_FILE.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"{_COUNTS_FILE}: root JSON must be an object")
    return data


def _save_counts(counts: dict[str, dict[str, int]]) -> None:
    _COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COUNTS_FILE.write_text(json.dumps(counts, indent=2), encoding="utf-8")


def _increment(provider: str) -> None:
    counts = _load_counts()
    month = _month_key()
    counts.setdefault(month, {})
    counts[month][provider] = counts[month].get(provider, 0) + 1
    _save_counts(counts)


def _get_usage(month: str) -> dict[str, int]:
    return _load_counts().get(month, {})


def _resolve_key(provider: str, config_keys: Optional[dict[str, str]]) -> str:
    if config_keys:
        override = config_keys.get(provider)
        if isinstance(override, str) and override.strip():
            return override.strip()
    env_value = os.getenv(_env_key_name(provider), "")
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()
    stored = load_secret(_secret_name(provider))
    if stored:
        return stored
    return ""


def _configured_keys(config_keys: Optional[dict[str, str]] = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for provider in _providers_order():
        key = _resolve_key(provider, config_keys)
        if key and not _is_invalid(provider):
            result[provider] = key
    return result


def select_provider(
    config_keys: Optional[dict[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    keys = _configured_keys(config_keys)
    if not keys:
        return None, None
    month = _month_key()
    usage = _get_usage(month)
    limit = _free_tier_limit()
    under_limit = [(p, k) for p, k in keys.items() if usage.get(p, 0) < limit]
    if under_limit:
        under_limit.sort(key=lambda item: usage.get(item[0], 0))
        provider, key = under_limit[0]
        logger.debug(
            "web_search_router: selected provider=%s usage=%d/%d",
            provider,
            usage.get(provider, 0),
            limit,
        )
        return provider, key
    first = next(iter(keys))
    logger.info(
        "web_search_router: all providers exceeded free-tier limit (%d) — using %s in paid mode",
        limit,
        first,
    )
    return first, keys[first]


class WebSearchProviderRejected(RuntimeError):
    def __init__(self, provider: str, status_code: int, reason: str) -> None:
        super().__init__(
            f"web_search provider {provider!r} rejected request: HTTP {status_code} {reason}"
        )
        self.provider = provider
        self.status_code = status_code
        self.reason = reason


def _http_response_status(exc: BaseException) -> Optional[int]:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    response = _require_requests().post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=_request_timeout_sec(),
    )
    response.raise_for_status()
    data = response.json()
    return [
        {
            "title": str(item.get("title") or ""),
            "href": str(item.get("url") or ""),
            "body": str(item.get("content") or ""),
        }
        for item in (data.get("results") or [])
    ]


def _search_exa(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    response = _require_requests().post(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "query": query,
            "numResults": max_results,
            "contents": {"text": {"maxCharacters": 1000}},
        },
        timeout=_request_timeout_sec(),
    )
    response.raise_for_status()
    data = response.json()
    items: list[dict[str, str]] = []
    for item in (data.get("results") or []):
        text = item.get("text") or item.get("summary") or ""
        items.append(
            {
                "title": str(item.get("title") or ""),
                "href": str(item.get("url") or ""),
                "body": str(text),
            }
        )
    return items


def _search_scrapingdog(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    response = _require_requests().get(
        "https://api.scrapingdog.com/google",
        params={
            "api_key": api_key,
            "query": query,
            "results": max_results,
            "country": "us",
        },
        timeout=_request_timeout_sec(),
    )
    response.raise_for_status()
    data = response.json()
    return [
        {
            "title": str(item.get("title") or ""),
            "href": str(item.get("link") or ""),
            "body": str(item.get("snippet") or ""),
        }
        for item in (data.get("organic_results") or [])
    ]


_SEARCH_FN = {
    "tavily": _search_tavily,
    "exa": _search_exa,
    "scrapingdog": _search_scrapingdog,
}


def web_search(
    query: str,
    max_results: int = 5,
    config_keys: Optional[dict[str, str]] = None,
) -> list[dict[str, str]]:
    provider, api_key = select_provider(config_keys)
    if provider is None or api_key is None:
        raise RuntimeError(
            "No web search API keys configured. "
            "Add a key via POST /v1/secrets or set one of the SWARM_* env vars listed in "
            "config/web_search.json."
        )
    search_fn = _SEARCH_FN.get(provider)
    if search_fn is None:
        raise RuntimeError(
            f"web_search.json: providers_order references unknown provider {provider!r}"
        )
    span_attributes = {"provider": provider, "max_results": max_results}
    try:
        with trace_span("web_search", attributes=span_attributes):
            results = search_fn(query, api_key, max_results)
    except Exception as exc:
        status = _http_response_status(exc)
        if status in (401, 403):
            _mark_invalid(provider)
            logger.warning(
                "web_search_router: provider=%s rejected key (HTTP %s); "
                "isolating key for %ds and surfacing failure",
                provider,
                status,
                _invalid_key_ttl_sec(),
            )
            record_activity(
                "web_searches",
                {
                    "provider": provider,
                    "query": query[:_MAX_QUERY_PREVIEW],
                    "status": "rejected",
                    "http_status": status,
                },
            )
            raise WebSearchProviderRejected(provider, status, str(exc)) from exc
        logger.warning("web_search_router: provider=%s failed: %s", provider, exc)
        record_activity(
            "web_searches",
            {
                "provider": provider,
                "query": query[:_MAX_QUERY_PREVIEW],
                "status": "error",
                "error": type(exc).__name__,
                "message": str(exc),
            },
        )
        raise
    _increment(provider)
    logger.info(
        "web_search_router: provider=%s query=%r results=%d",
        provider,
        query[:80],
        len(results),
    )
    record_activity(
        "web_searches",
        {
            "provider": provider,
            "query": query[:_MAX_QUERY_PREVIEW],
            "status": "ok",
            "hits": [
                {"title": hit.get("title", ""), "url": hit.get("href", "")}
                for hit in results[:10]
            ],
            "hit_count": len(results),
        },
    )
    return results


def web_search_available(config_keys: Optional[dict[str, str]] = None) -> bool:
    return bool(_configured_keys(config_keys))


def web_search_mcp_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using the configured search provider "
                "(Tavily / Exa / ScrapingDog, auto-rotated). "
                "Use for finding current information, documentation, examples."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                },
                "required": ["query"],
            },
        },
    }


def get_active_provider_info() -> dict[str, Any]:
    month = _month_key()
    usage = _get_usage(month)
    keys = _configured_keys()
    return {
        "month": month,
        "configured_providers": list(keys.keys()),
        "usage": {p: usage.get(p, 0) for p in keys},
        "free_tier_limit": _free_tier_limit(),
        "invalidated": sorted(_invalid_until.keys()),
    }
