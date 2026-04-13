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
    role: str
    model_id: str
    provider: str
    reason: str
    remote_profile: str = ""


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


_SIZE_MARKERS: list[tuple[str, int]] = [
    ("405b", 405), ("236b", 236), ("110b", 110), ("72b", 72), ("70b", 70),
    ("34b", 34), ("32b", 32), ("27b", 27), ("22b", 22), ("20b", 20),
    ("14b", 14), ("13b", 13), ("9b", 9), ("8b", 8), ("7b", 7),
    ("4b", 4), ("3b", 3), ("2b", 2), ("1b", 1), ("0.5b", 0),
]


def _extract_model_size(model_id: str) -> int:
    """Heuristic: extract approximate parameter count from model name."""
    mid = model_id.lower().replace("-", "").replace("_", "")
    for marker, size in _SIZE_MARKERS:
        if marker.replace("-", "").replace("_", "") in mid:
            return size
    return 0


def _role_tier(role: str) -> str:
    """Return 'heavy', 'medium', or 'light' for a role from policy."""
    tiers = load_role_model_policy().get("role_tiers") or {}
    for tier_name, roles_list in tiers.items():
        if role in (roles_list or []):
            return tier_name
    return "medium"


def _provider_remote_profile(provider: str) -> str:
    """Return the remote_profile name for a cloud provider from policy."""
    mapping = load_role_model_policy().get("provider_remote_profiles") or {}
    return str(mapping.get(provider, "")).strip()


def _score_model_for_role(
    model: DiscoveredModel,
    keywords: list[str],
    tier: str,
    assignment_counts: dict[str, int],
) -> tuple[float, str]:
    """Score a model for a role. Returns (score, reason)."""
    score = 0.0
    reason_parts: list[str] = []
    mid = model.model_id.lower()

    # 1. Keyword match bonus (earlier keywords in list = higher priority)
    for i, kw in enumerate(keywords):
        if kw in mid:
            score += 10.0 - i * 0.5
            reason_parts.append(f"keyword '{kw}'")
            break

    # 2. Model size heuristic — score according to tier needs
    model_size = _extract_model_size(model.model_id)
    if tier == "heavy" and model_size > 0:
        score += min(model_size, 70) * 0.1
        reason_parts.append(f"size {model_size}B (heavy tier)")
    elif tier == "light" and model_size > 0:
        score += max(0, 30 - model_size) * 0.1
        reason_parts.append(f"size {model_size}B (light tier)")
    elif model_size > 0:
        score += 1.0
        reason_parts.append(f"size {model_size}B")

    # 3. Local preference: local providers get a bonus over cloud
    if model.provider in ("ollama", "lm_studio"):
        score += 2.0
        reason_parts.append(f"local ({model.provider})")
    else:
        reason_parts.append(f"cloud ({model.provider})")

    # 4. Diversity penalty: slight penalty if this model is already heavily assigned
    usage = assignment_counts.get(model.model_id, 0)
    if usage > 0:
        score -= usage * 0.3
        reason_parts.append(f"diversity penalty ({usage} prior)")

    reason = ", ".join(reason_parts) if reason_parts else "default"
    return score, reason


def assign_models_to_roles(models: list[DiscoveredModel]) -> list[ModelAssignment]:
    """Assign best available model to each agent role using scored ranking."""
    assignments: list[ModelAssignment] = []
    role_keywords = _policy_keywords()
    assignment_counts: dict[str, int] = {}

    local_models = [m for m in models if m.provider in ("ollama", "lm_studio")]
    cloud_models = [m for m in models if m.provider not in ("ollama", "lm_studio")]
    has_anthropic = any(m.provider == "anthropic" for m in models)
    has_openai = any(m.provider == "openai" for m in models)

    for role in _policy_roles():
        tier = _role_tier(role)
        keywords = role_keywords.get(role, [])
        best: Optional[tuple[float, DiscoveredModel, str]] = None

        # Score all local models
        for m in local_models:
            sc, reason = _score_model_for_role(m, keywords, tier, assignment_counts)
            if best is None or sc > best[0]:
                best = (sc, m, reason)

        # Score cloud models too (but with lower base priority)
        for m in cloud_models:
            sc, reason = _score_model_for_role(m, keywords, tier, assignment_counts)
            if best is None or sc > best[0]:
                best = (sc, m, reason)

        # If no scored candidate found, try cloud defaults directly
        if best is None:
            if has_anthropic:
                mid, prov = _cloud_default("anthropic", role)
                if mid:
                    best = (0.0, DiscoveredModel(model_id=mid, provider=prov), "cloud default (Anthropic)")
            if best is None and has_openai:
                mid, prov = _cloud_default("openai", role)
                if mid:
                    best = (0.0, DiscoveredModel(model_id=mid, provider=prov), "cloud default (OpenAI)")

        if best:
            _, chosen_model, reason = best
            remote_profile = _provider_remote_profile(chosen_model.provider)
            assignments.append(ModelAssignment(
                role=role,
                model_id=chosen_model.model_id,
                provider=chosen_model.provider,
                reason=reason,
                remote_profile=remote_profile,
            ))
            assignment_counts[chosen_model.model_id] = assignment_counts.get(chosen_model.model_id, 0) + 1
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
