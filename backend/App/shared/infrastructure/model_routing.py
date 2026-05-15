
from __future__ import annotations

import logging
import os
from typing import Optional

__all__ = [
    "anthropic_model_prefixes",
    "detect_provider",
    "is_cloud_model",
    "should_use_anthropic_backend",
]

logger = logging.getLogger(__name__)


def anthropic_model_prefixes() -> tuple[str, ...]:
    return tuple(
        p.strip()
        for p in os.getenv("SWARM_CLOUD_MODEL_PREFIXES", "claude,anthropic/").split(",")
        if p.strip()
    )


def is_cloud_model(model: str) -> bool:
    name = (model or "").strip()
    return any(name.startswith(p) for p in anthropic_model_prefixes())


def should_use_anthropic_backend(model: str, llm_route: Optional[str] = None) -> bool:
    route = (llm_route or "").strip().lower()
    if route == "openai":
        return False
    if route == "anthropic":
        return True
    return is_cloud_model(model)


def detect_provider(
    model: str,
    *,
    remote_provider: Optional[str] = None,
    environment: Optional[str] = None,
) -> str:
    env_key = (environment or "").lower()
    explicit = (remote_provider or "").strip().lower()
    if env_key == "anthropic":
        return explicit or "anthropic"
    if explicit:
        return explicit
    m = (model or "").strip().lower()
    if m.startswith("gemini"):
        return "gemini"
    if (
        m.startswith("gpt")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("chatgpt")
        or m.startswith("openai/")
    ):
        return "openai_compatible"
    logger.warning(
        "Unknown cloud model prefix for %r, defaulting to anthropic backend — "
        "set SWARM_REMOTE_PROVIDER to override",
        model,
    )
    return "anthropic"
