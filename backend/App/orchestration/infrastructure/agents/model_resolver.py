
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from backend.App.shared.domain.exceptions import PrivacyTierViolation
from backend.App.shared.infrastructure.env_flags import is_truthy_env

logger = logging.getLogger(__name__)

_VALID_PRIVACY_TIERS = frozenset({"public", "internal", "secret"})


def resolve_model(role: str, role_default_model: str) -> str:
    role_key = role.upper()

    route_specific = os.getenv(f"SWARM_ROUTE_{role_key}")
    route = route_specific.lower() if route_specific else ""
    _route_planning_roles = {
        "PM",
        "BA",
        "ARCH",
        "REVIEWER",
        "STACK_REVIEWER",
        "DEV_LEAD",
        "DOC_GEN",
        "PROBLEM_SPOTTER",
        "REFACTOR_PLAN",
        "CODE_DIAGRAM",
    }
    _model_planning_roles = _route_planning_roles - {"PM"}
    if not route:
        if role_key in _route_planning_roles:
            route = os.getenv("SWARM_ROUTE_PLANNING", "").lower()
        elif role_key in {"DEV", "QA", "DEVOPS"}:
            route = os.getenv("SWARM_ROUTE_BUILD", "").lower()
    if not route:
        route = os.getenv("SWARM_ROUTE_DEFAULT", "local").lower()

    if route == "cloud":
        specific_cloud = os.getenv(f"SWARM_MODEL_CLOUD_{role_key}")
        if specific_cloud:
            return specific_cloud.strip()

        cloud_ba_arch = os.getenv("SWARM_MODEL_CLOUD_BA_ARCH", "").strip()
        if (
            role_key in {
                "BA",
                "ARCH",
                "REVIEWER",
                "STACK_REVIEWER",
                "REFACTOR_PLAN",
                "CODE_DIAGRAM",
                "DOC_GEN",
            }
            and cloud_ba_arch
        ):
            return cloud_ba_arch

        if role_key in _model_planning_roles:
            cloud_planning = os.getenv("SWARM_MODEL_CLOUD_PLANNING", "").strip()
            if cloud_planning:
                return cloud_planning
        if role_key in {"DEV", "QA", "DEVOPS"}:
            cloud_build = os.getenv("SWARM_MODEL_CLOUD_BUILD", "").strip()
            if cloud_build:
                return cloud_build

        from backend.App.integrations.infrastructure.llm.config import SWARM_MODEL_CLOUD_DEFAULT
        return os.getenv("SWARM_MODEL_CLOUD", SWARM_MODEL_CLOUD_DEFAULT).strip() or SWARM_MODEL_CLOUD_DEFAULT

    specific = os.getenv(f"SWARM_MODEL_{role_key}")
    if specific:
        return specific.strip()

    ba_arch = os.getenv("SWARM_MODEL_BA_ARCH", "").strip()
    if role_key in {
        "BA",
        "ARCH",
        "REVIEWER",
        "STACK_REVIEWER",
        "REFACTOR_PLAN",
        "CODE_DIAGRAM",
        "DOC_GEN",
    } and ba_arch:
        return ba_arch

    if role_key in _model_planning_roles:
        planning = os.getenv("SWARM_MODEL_PLANNING", "").strip()
        if planning:
            return planning

    if role_key in {"DEV", "QA", "DEVOPS"}:
        build = os.getenv("SWARM_MODEL_BUILD", "").strip()
        if build:
            return build

    return os.getenv("SWARM_MODEL", role_default_model).strip() or role_default_model


def _retention_config_path() -> Path:
    override = (os.getenv("SWARM_PROVIDERS_RETENTION_PATH") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve()
    return (Path(__file__).resolve().parents[5] / "config" / "providers_retention.json")


def _load_retention_config() -> dict[str, Any]:
    path = _retention_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"providers retention config not found at {path}. Required for privacy "
            f"tier enforcement; create it or set SWARM_PROVIDERS_RETENTION_PATH."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"failed to load providers retention config {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"providers retention config {path} must be a JSON object"
        )
    return data


def _infer_provider_from_model(model: str) -> str:
    name = (model or "").strip().lower()
    if not name:
        return ""
    if name.startswith("claude") or "anthropic" in name:
        return "anthropic"
    if name.startswith(("gpt-", "o1", "o3", "o4")) or "openai" in name:
        return "openai"
    if "ollama" in name:
        return "ollama"
    if "lmstudio" in name or "lm-studio" in name:
        return "lmstudio"
    if name.startswith("gemini") or "google" in name:
        return "google"
    if name.startswith("deepseek"):
        return "deepseek"
    if name.startswith(("llama", "qwen", "mistral", "mixtral", "phi")):
        return "ollama"
    return ""


def _provider_meta(provider: str) -> dict[str, Any]:
    config = _load_retention_config()
    providers = config.get("providers") or {}
    if not isinstance(providers, dict):
        return {}
    entry = providers.get(provider) or {}
    return entry if isinstance(entry, dict) else {}


def enforce_privacy_tier(role: str, model: str, tier: str) -> None:
    normalised = (tier or "public").strip().lower()
    if normalised not in _VALID_PRIVACY_TIERS:
        raise PrivacyTierViolation(
            tier=normalised,
            provider="",
            remediation=(
                f"unknown privacy tier '{tier}'; expected one of "
                f"{sorted(_VALID_PRIVACY_TIERS)}"
            ),
        )
    if normalised == "public":
        return
    provider = _infer_provider_from_model(model)
    meta = _provider_meta(provider)
    is_local = bool(meta.get("local", False))
    retention = str(meta.get("data_retention") or "").strip().lower()
    if normalised == "secret":
        if not is_local:
            raise PrivacyTierViolation(
                tier="secret",
                provider=provider or model,
                remediation=(
                    f"role '{role}' is marked secret; pick a local provider "
                    f"(ollama / lmstudio) for model '{model}'."
                ),
            )
        return
    if not is_local and retention != "none":
        raise PrivacyTierViolation(
            tier="internal",
            provider=provider or model,
            remediation=(
                f"role '{role}' is marked internal; provider '{provider or model}' "
                f"retains data ('{retention or 'unknown'}'). Use a local or "
                f"no-retention provider."
            ),
        )


def _apply_local_preference(role: str, current_model: str, privacy: str) -> str:
    from backend.App.integrations.infrastructure.model_discovery import (
        pick_best_local_model,
    )

    best = pick_best_local_model()
    if best is not None:
        logger.info(
            "model_resolver: SWARM_PREFER_LOCAL=1 — role=%s using local provider=%s model=%s "
            "(was %s)",
            role,
            best.provider,
            best.model_id,
            current_model,
        )
        return best.model_id
    if privacy == "secret":
        raise PrivacyTierViolation(
            tier="secret",
            provider="",
            remediation=(
                f"SWARM_PREFER_LOCAL=1 and no local provider is reachable; "
                f"role '{role}' is marked secret so cloud fallback is forbidden. "
                f"Start lm_studio at http://localhost:1234 or ollama at http://localhost:11434."
            ),
        )
    logger.warning(
        "model_resolver: SWARM_PREFER_LOCAL=1 — no local provider reachable for role=%s; "
        "keeping configured model %r (privacy=%s allows cloud)",
        role,
        current_model,
        privacy,
    )
    return current_model


def resolve_model_with_privacy(
    role: str,
    role_default_model: str,
    *,
    privacy: str = "public",
    agent_config: Optional[dict[str, Any]] = None,
) -> str:
    effective_privacy = (privacy or "public").strip().lower()
    if agent_config:
        role_entry = agent_config.get(role)
        if isinstance(role_entry, dict):
            override = role_entry.get("privacy")
            if isinstance(override, str) and override.strip():
                effective_privacy = override.strip().lower()
    model = resolve_model(role, role_default_model)
    if is_truthy_env("SWARM_PREFER_LOCAL", default=False):
        model = _apply_local_preference(role, model, effective_privacy)
    if not is_truthy_env("SWARM_COST_TIER_DISABLED", default=False):
        from backend.App.integrations.infrastructure.cost_tier_resolver import (
            enforce_role_tier,
            load_cost_tier_config,
            pick_default_model_for_role,
        )

        cfg = load_cost_tier_config()
        if role in cfg.get("role_policies", {}) and model == role_default_model:
            substitute = pick_default_model_for_role(role)
            if substitute:
                model = substitute
        enforce_role_tier(role, model)
    enforce_privacy_tier(role, model, effective_privacy)
    return model


def resolve_base_url(role: str) -> Optional[str]:
    role_key = role.upper()
    specific = os.getenv(f"SWARM_BASE_URL_{role_key}", "").strip()
    if specific:
        return specific
    generic = os.getenv("SWARM_BASE_URL", "").strip()
    return generic if generic else None
