"""Discover available LLM models from local and cloud providers."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from backend.App.orchestration.infrastructure.agents.role_model_policy import load_role_model_policy

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234")
DISCOVERY_TIMEOUT = float(os.getenv("SWARM_MODEL_DISCOVERY_TIMEOUT_SECS", "3.0"))

# §10.3-3: TTL cache for model lists — reduces noise from repeated /v1/models calls
_MODEL_LIST_CACHE_TTL_SEC = float(os.getenv("SWARM_MODEL_LIST_CACHE_TTL_SEC", "30"))
_model_cache: dict[str, tuple[float, list]] = {}  # provider → (timestamp, models)
_model_cache_lock = threading.Lock()
_model_backoff_until: dict[str, float] = {}
_MODEL_DISCOVERY_BACKOFF_SEC = float(os.getenv("SWARM_MODEL_DISCOVERY_BACKOFF_SEC", "15"))
_discovery_metrics: dict[str, dict[str, int]] = {
    "ollama": {"network_calls": 0, "cache_hits": 0, "models_returned": 0},
    "lm_studio": {"network_calls": 0, "cache_hits": 0, "models_returned": 0},
}


@dataclass
class DiscoveredModel:
    model_id: str
    provider: str  # ollama | lm_studio | openai | anthropic
    context_length: Optional[int] = None
    is_available: bool = True


@dataclass
class ModelAssignment:
    role: str  # pm | dev | qa | critic | planner | onboarding
    model_id: str
    provider: str
    reason: str


def _cached(provider: str, fetcher) -> list[DiscoveredModel]:
    """Return cached model list if fresh, otherwise call fetcher and cache."""
    now = time.monotonic()
    with _model_cache_lock:
        cached = _model_cache.get(provider)
        if cached and (now - cached[0]) < _MODEL_LIST_CACHE_TTL_SEC:
            if provider in _discovery_metrics:
                _discovery_metrics[provider]["cache_hits"] += 1
                _discovery_metrics[provider]["models_returned"] += len(cached[1])
            return cached[1]
        backoff_until = _model_backoff_until.get(provider, 0.0)
        if now < backoff_until:
            return cached[1] if cached else []

    if provider in _discovery_metrics:
        _discovery_metrics[provider]["network_calls"] += 1
    result = fetcher()

    with _model_cache_lock:
        if result:
            _model_cache[provider] = (time.monotonic(), result)
            _model_backoff_until.pop(provider, None)
            if provider in _discovery_metrics:
                _discovery_metrics[provider]["models_returned"] += len(result)
        else:
            _model_backoff_until[provider] = time.monotonic() + _MODEL_DISCOVERY_BACKOFF_SEC
        return result or (cached[1] if cached else [])


def _fetch_ollama_models() -> list[DiscoveredModel]:
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=DISCOVERY_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return [
                DiscoveredModel(model_id=m["name"], provider="ollama")
                for m in data.get("models", [])
            ]
    except Exception as exc:
        logger.debug("model_discovery: ollama not available: %s", exc)
    return []


def discover_ollama_models() -> list[DiscoveredModel]:
    """Fetch models from local Ollama instance (cached for SWARM_MODEL_LIST_CACHE_TTL_SEC)."""
    return _cached("ollama", _fetch_ollama_models)


def _fetch_lm_studio_models() -> list[DiscoveredModel]:
    try:
        resp = httpx.get(f"{LM_STUDIO_BASE_URL}/v1/models", timeout=DISCOVERY_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return [
                DiscoveredModel(model_id=m["id"], provider="lm_studio")
                for m in data.get("data", [])
            ]
    except Exception as exc:
        logger.debug("model_discovery: lm_studio not available: %s", exc)
    return []


def discover_lm_studio_models() -> list[DiscoveredModel]:
    """Fetch models from local LM Studio instance (cached for SWARM_MODEL_LIST_CACHE_TTL_SEC)."""
    return _cached("lm_studio", _fetch_lm_studio_models)


def discover_cloud_models() -> list[DiscoveredModel]:
    """Check cloud providers based on available API keys."""
    models: list[DiscoveredModel] = []
    if os.getenv("ANTHROPIC_API_KEY"):
        _anthropic_models = os.getenv(
            "SWARM_ANTHROPIC_MODELS", "claude-opus-4-5,claude-sonnet-4-5,claude-haiku-4-5"
        ).split(",")
        for m in _anthropic_models:
            if m.strip():
                models.append(DiscoveredModel(model_id=m.strip(), provider="anthropic"))
    if os.getenv("OPENAI_API_KEY"):
        _openai_models = os.getenv(
            "SWARM_OPENAI_MODELS", "gpt-4o,gpt-4o-mini,gpt-4-turbo"
        ).split(",")
        for m in _openai_models:
            if m.strip():
                models.append(DiscoveredModel(model_id=m.strip(), provider="openai"))
    return models


def discover_all_models() -> list[DiscoveredModel]:
    """Discover models from all available providers."""
    all_models: list[DiscoveredModel] = []
    all_models.extend(discover_ollama_models())
    all_models.extend(discover_lm_studio_models())
    all_models.extend(discover_cloud_models())
    logger.info("model_discovery: found %d models total", len(all_models))
    return all_models


def _policy_roles() -> list[str]:
    raw = load_role_model_policy().get("roles") or []
    roles = [str(item).strip() for item in raw if str(item).strip()]
    return roles or ["pm", "dev", "qa", "critic", "planner", "onboarding"]


def _policy_keywords() -> dict[str, list[str]]:
    raw = load_role_model_policy().get("local_keyword_preferences") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(role).strip(): [str(item).strip().lower() for item in values or [] if str(item).strip()]
        for role, values in raw.items()
    }


def _cloud_default(provider: str, role: str) -> tuple[str, str]:
    defaults = load_role_model_policy().get("cloud_defaults") or {}
    provider_defaults = defaults.get(provider) if isinstance(defaults, dict) else None
    model_id = ""
    if isinstance(provider_defaults, dict):
        model_id = str(provider_defaults.get(role) or "").strip()
    legacy_env = ""
    if provider == "anthropic":
        legacy_env = os.getenv(f"SWARM_CLOUD_{role.upper()}_MODEL", "").strip()
    env_override = os.getenv(f"SWARM_{provider.upper()}_{role.upper()}_MODEL", "").strip()
    if env_override:
        model_id = env_override
    elif legacy_env:
        model_id = legacy_env
    return model_id, provider


def assign_models_to_roles(models: list[DiscoveredModel]) -> list[ModelAssignment]:
    """Assign best available model to each agent role."""
    assignments: list[ModelAssignment] = []
    role_keywords = _policy_keywords()

    local_models = [m for m in models if m.provider in ("ollama", "lm_studio")]
    has_anthropic = any(m.provider == "anthropic" for m in models)
    has_openai = any(m.provider == "openai" for m in models)

    for role in _policy_roles():
        assigned: Optional[ModelAssignment] = None
        keywords = role_keywords.get(role, [])

        # 1. Try local models matching keywords
        for kw in keywords:
            match = next((m for m in local_models if kw in m.model_id.lower()), None)
            if match:
                assigned = ModelAssignment(
                    role=role,
                    model_id=match.model_id,
                    provider=match.provider,
                    reason=f"local model matching '{kw}' keyword",
                )
                break

        # 2. Fallback: any local model
        if not assigned and local_models:
            m = local_models[0]
            assigned = ModelAssignment(
                role=role,
                model_id=m.model_id,
                provider=m.provider,
                reason="first available local model",
            )

        # 3. Cloud fallback (Anthropic > OpenAI)
        if not assigned and has_anthropic:
            mid, prov = _cloud_default("anthropic", role)
            assigned = ModelAssignment(
                role=role, model_id=mid, provider=prov, reason="cloud fallback (Anthropic)"
            )
        if not assigned and has_openai:
            mid, prov = _cloud_default("openai", role)
            assigned = ModelAssignment(
                role=role, model_id=mid, provider=prov, reason="cloud fallback (OpenAI)"
            )

        if assigned:
            assignments.append(assigned)
        else:
            logger.warning("model_discovery: no model found for role %s", role)

    return assignments


def save_models_config(workspace_root: str, assignments: list[ModelAssignment]) -> None:
    """Save model assignments to .swarm/models_config.json."""
    swarm_dir = Path(workspace_root) / ".swarm"
    swarm_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "version": "1",
        "roles": {
            a.role: {
                "model_id": a.model_id,
                "provider": a.provider,
                "reason": a.reason,
            }
            for a in assignments
        },
    }
    (swarm_dir / "models_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    logger.info(
        "model_discovery: saved models_config.json with %d role assignments", len(assignments)
    )


def load_models_config(workspace_root: str) -> Optional[dict]:
    """Load .swarm/models_config.json if it exists."""
    path = Path(workspace_root) / ".swarm" / "models_config.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def discovery_metrics_snapshot() -> dict[str, dict[str, int]]:
    with _model_cache_lock:
        return {
            provider: dict(values)
            for provider, values in _discovery_metrics.items()
        }


def reset_discovery_metrics_for_tests() -> None:
    with _model_cache_lock:
        _model_cache.clear()
        _model_backoff_until.clear()
        for provider in _discovery_metrics:
            _discovery_metrics[provider] = {"network_calls": 0, "cache_hits": 0, "models_returned": 0}
