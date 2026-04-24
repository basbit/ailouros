from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_CONTEXT_SIZE_CACHE: dict[str, int] = {}
_KNOWN_MODEL_CONTEXTS: dict[str, int] = {
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-3.5-turbo": 16384,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "gemini-1.5-pro": 1000000,
    "gemini-1.5-flash": 1000000,
    "gemini-2.0-flash": 1000000,
    "gpt-oss": 32768,
    "qwen3": 32768,
    "qwen2": 32768,
    "llama-3": 131072,
    "llama3": 131072,
    "gemma-4": 32768,
    "gemma-3": 32768,
    "phi-4": 16384,
    "deepseek": 65536,
}


def _query_ollama_context(model: str) -> Optional[int]:
    try:
        import httpx
        base = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OPENAI_BASE_URL", "").replace("/v1", "") or "http://localhost:11434").rstrip("/")
        resp = httpx.post(f"{base}/api/show", json={"model": model}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            model_info = data.get("model_info") or {}
            for key, val in model_info.items():
                if "context" in key.lower() and isinstance(val, (int, float)):
                    return int(val)
            params = data.get("parameters") or ""
            if "num_ctx" in params:
                for line in params.split("\n"):
                    if "num_ctx" in line:
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[-1].isdigit():
                            return int(parts[-1])
    except Exception as exc:
        logger.debug("context_size_resolver: Ollama query failed: %s", exc)
    return None


def _match_known_model(model: str) -> Optional[int]:
    model_lower = model.lower()
    for pattern, ctx in _KNOWN_MODEL_CONTEXTS.items():
        if pattern in model_lower:
            return ctx
    return None


def resolve_context_size(model: str, environment: str = "") -> int:
    env_value = os.getenv("SWARM_MODEL_CONTEXT_SIZE", "").strip()
    if env_value.isdigit() and int(env_value) > 0:
        return int(env_value)

    cache_key = f"{environment}:{model}"
    if cache_key in _CONTEXT_SIZE_CACHE:
        return _CONTEXT_SIZE_CACHE[cache_key]

    env_lower = (environment or "").lower()
    if env_lower in ("ollama", ""):
        ollama_ctx = _query_ollama_context(model)
        if ollama_ctx and ollama_ctx > 0:
            _CONTEXT_SIZE_CACHE[cache_key] = ollama_ctx
            logger.info("context_size_resolver: %s=%d tokens (from Ollama)", model, ollama_ctx)
            return ollama_ctx

    if env_lower in ("cloud", "anthropic", "openai", "gemini"):
        known = _match_known_model(model)
        if known:
            _CONTEXT_SIZE_CACHE[cache_key] = known
            logger.info("context_size_resolver: %s=%d tokens (known model)", model, known)
            return known

    known = _match_known_model(model)
    if known:
        _CONTEXT_SIZE_CACHE[cache_key] = known
        logger.info("context_size_resolver: %s=%d tokens (known local model)", model, known)
        return known

    default = 16384
    logger.debug(
        "context_size_resolver: using default %d tokens for model=%s env=%s. "
        "Set SWARM_MODEL_CONTEXT_SIZE for explicit control.",
        default, model, environment,
    )
    return default
