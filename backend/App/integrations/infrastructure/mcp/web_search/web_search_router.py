"""Multi-provider web search router with monthly usage-based rotation.

Providers (in priority order):
  1. Tavily      — SWARM_TAVILY_API_KEY       (1000 req/month free)
  2. Exa         — SWARM_EXA_API_KEY          (1000 req/month free)
  3. ScrapingDog — SWARM_SCRAPINGDOG_API_KEY  (1000 req/month free)

Rotation logic:
  - Only providers with a configured API key participate.
  - Monthly usage is tracked in ~/.swarm/web_search_counts.json.
  - Each request goes to the provider with the lowest monthly usage that
    is still under the free-tier limit (1000 req/month).
  - Once ALL configured providers have reached 1000 req/month the router
    falls back to Tavily (first provider in priority order) — same key,
    now in paid mode.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests  # type: ignore[import-untyped]
except ImportError as _requests_import_error:  # explicit named binding
    requests = None  # type: ignore[assignment]
    _REQUESTS_IMPORT_ERROR: Optional[BaseException] = _requests_import_error
else:
    _REQUESTS_IMPORT_ERROR = None


def _require_requests() -> Any:
    """Return the ``requests`` module or raise an actionable error."""
    if requests is None:
        raise RuntimeError(
            "'requests' package is required for web search providers — "
            "install with `pip install requests`. "
            f"Original import error: {_REQUESTS_IMPORT_ERROR!r}"
        )
    return requests


logger = logging.getLogger(__name__)

FREE_TIER_LIMIT = 1_000  # requests per provider per calendar month

# ~/.swarm/web_search_counts.json  → {"2026-04": {"tavily": 12, "exa": 0, ...}}
_COUNTS_FILE = Path.home() / ".swarm" / "web_search_counts.json"

_PROVIDERS = ("tavily", "exa", "scrapingdog")
_ENV_KEYS = {
    "tavily": "SWARM_TAVILY_API_KEY",
    "exa": "SWARM_EXA_API_KEY",
    "scrapingdog": "SWARM_SCRAPINGDOG_API_KEY",
}


# ---------------------------------------------------------------------------
# Counter helpers
# ---------------------------------------------------------------------------

def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_counts() -> dict[str, dict[str, int]]:
    try:
        if _COUNTS_FILE.exists():
            data = json.loads(_COUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_counts(counts: dict[str, dict[str, int]]) -> None:
    try:
        _COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COUNTS_FILE.write_text(json.dumps(counts, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("web_search_router: failed to save counts: %s", exc)


def _increment(provider: str) -> None:
    counts = _load_counts()
    month = _month_key()
    counts.setdefault(month, {})
    counts[month][provider] = counts[month].get(provider, 0) + 1
    _save_counts(counts)


def _get_usage(month: str) -> dict[str, int]:
    return _load_counts().get(month, {})


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _configured_keys(config_keys: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return {provider: api_key} for all providers that have a key set."""
    result: dict[str, str] = {}
    for provider in _PROVIDERS:
        env_var = _ENV_KEYS[provider]
        key = (
            (config_keys or {}).get(provider)
            or os.getenv(env_var, "")
            or ""
        ).strip()
        if key:
            result[provider] = key
    return result


def select_provider(
    config_keys: Optional[dict[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return (provider_name, api_key) for the best available provider.

    Selection order:
      1. Provider with a key AND usage < FREE_TIER_LIMIT, fewest requests first.
      2. If all are at limit → first configured provider (paid fallback).
      3. If no keys configured → (None, None).
    """
    keys = _configured_keys(config_keys)
    if not keys:
        return None, None

    month = _month_key()
    usage = _get_usage(month)

    # Providers under the free tier limit, sorted by ascending usage
    under_limit = [
        (p, k)
        for p, k in keys.items()
        if usage.get(p, 0) < FREE_TIER_LIMIT
    ]
    if under_limit:
        under_limit.sort(key=lambda t: usage.get(t[0], 0))
        provider, key = under_limit[0]
        logger.debug(
            "web_search_router: selected provider=%s usage=%d/%d",
            provider,
            usage.get(provider, 0),
            FREE_TIER_LIMIT,
        )
        return provider, key

    # All at limit — paid fallback to first configured provider
    first_provider = next(p for p in _PROVIDERS if p in keys)
    logger.info(
        "web_search_router: all providers at free-tier limit — "
        "falling back to %s (paid mode)",
        first_provider,
    )
    return first_provider, keys[first_provider]


# ---------------------------------------------------------------------------
# Search implementations
# ---------------------------------------------------------------------------

def _search_tavily(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    _req = _require_requests()
    resp = _req.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in (data.get("results") or []):
        results.append({
            "title": str(item.get("title") or ""),
            "href": str(item.get("url") or ""),
            "body": str(item.get("content") or ""),
        })
    return results


def _search_exa(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    _req = _require_requests()
    resp = _req.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={"query": query, "numResults": max_results, "contents": {"text": {"maxCharacters": 1000}}},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in (data.get("results") or []):
        text = (item.get("text") or item.get("summary") or "")
        results.append({
            "title": str(item.get("title") or ""),
            "href": str(item.get("url") or ""),
            "body": str(text),
        })
    return results


def _search_scrapingdog(query: str, api_key: str, max_results: int) -> list[dict[str, str]]:
    _req = _require_requests()
    resp = _req.get(
        "https://api.scrapingdog.com/google",
        params={
            "api_key": api_key,
            "query": query,
            "results": max_results,
            "country": "us",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in (data.get("organic_results") or []):
        results.append({
            "title": str(item.get("title") or ""),
            "href": str(item.get("link") or ""),
            "body": str(item.get("snippet") or ""),
        })
    return results


_SEARCH_FN = {
    "tavily": _search_tavily,
    "exa": _search_exa,
    "scrapingdog": _search_scrapingdog,
}


# ---------------------------------------------------------------------------
# Public search entry point
# ---------------------------------------------------------------------------

def web_search(
    query: str,
    max_results: int = 5,
    config_keys: Optional[dict[str, str]] = None,
) -> list[dict[str, str]]:
    """Execute a web search using the best available provider.

    Returns list of {title, href, body} dicts, same shape as ddg_search.
    Raises RuntimeError if no provider keys are configured.
    """
    provider, api_key = select_provider(config_keys)
    if provider is None or api_key is None:
        raise RuntimeError(
            "No web search API keys configured. "
            "Set at least one of: SWARM_TAVILY_API_KEY, SWARM_EXA_API_KEY, "
            "SWARM_SCRAPINGDOG_API_KEY."
        )

    search_fn = _SEARCH_FN[provider]
    try:
        results = search_fn(query, api_key, max_results)
        _increment(provider)
        logger.info(
            "web_search_router: provider=%s query=%r results=%d",
            provider,
            query[:80],
            len(results),
        )
        return results
    except Exception as exc:
        logger.warning("web_search_router: provider=%s failed: %s", provider, exc)
        raise


def web_search_available(config_keys: Optional[dict[str, str]] = None) -> bool:
    """Return True if at least one provider has an API key configured."""
    return bool(_configured_keys(config_keys))


# ---------------------------------------------------------------------------
# MCP config helpers (for auto.py / setup.py)
# ---------------------------------------------------------------------------

def web_search_mcp_tool_definition() -> dict[str, Any]:
    """Return an OpenAI-compatible tool definition for the web search router."""
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
    """Return info about the current provider selection (for logging/UI)."""
    month = _month_key()
    usage = _get_usage(month)
    keys = _configured_keys()
    return {
        "month": month,
        "configured_providers": list(keys.keys()),
        "usage": {p: usage.get(p, 0) for p in keys},
        "free_tier_limit": FREE_TIER_LIMIT,
    }
