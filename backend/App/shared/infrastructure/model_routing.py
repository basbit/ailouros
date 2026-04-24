"""Model / provider detection used when routing an LLM call.

Three separate heuristics used to live in:

  * ``integrations/infrastructure/llm/providers.py`` — ``_is_cloud_model`` +
    ``_use_anthropic_backend`` (prefix matching against ``claude,anthropic/``)
  * ``orchestration/infrastructure/agents/base_agent.py`` —
    ``effective_cloud_provider`` (detects gemini/gpt/o1/openai-compat)
  * ``integrations/.../mcp/openai_loop/loop.py`` — ``_build_openai_client_for_env``
    (env-first routing: lmstudio / ollama / cloud)

This module centralises the model-name matching so all call sites agree on
what "anthropic" vs "openai_compatible" vs "gemini" means. The env-aware
client builder stays where it is (it depends on the concrete OpenAI client
factory), but it now asks us for the provider-id string.

Precedence we use everywhere (highest → lowest):
  1. Explicit route / provider string (``llm_route="openai"`` etc.)
  2. Model name prefix (``claude-…`` → anthropic, ``gemini-…`` → gemini, …)
  3. Environment fallback (``environment="anthropic"`` → anthropic)
"""

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
    """Return the list of model-name prefixes that mean "Anthropic cloud"."""
    return tuple(
        p.strip()
        for p in os.getenv("SWARM_CLOUD_MODEL_PREFIXES", "claude,anthropic/").split(",")
        if p.strip()
    )


def is_cloud_model(model: str) -> bool:
    """``True`` if the model name looks like an Anthropic cloud model."""
    name = (model or "").strip()
    return any(name.startswith(p) for p in anthropic_model_prefixes())


def should_use_anthropic_backend(model: str, llm_route: Optional[str] = None) -> bool:
    """Route decision: call the Anthropic SDK for ``model``?

    ``llm_route`` ("openai"/"anthropic"/…) is an explicit override and wins
    over any model-name inference.
    """
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
    """Resolve the provider id used by the OpenAI-compatible transport layer.

    Returns one of ``"anthropic"``, ``"openai_compatible"``, ``"gemini"``,
    or whatever explicit ``remote_provider`` string the caller supplied.
    Matches the behaviour of the previous ``effective_cloud_provider`` helper
    in ``orchestration/.../base_agent.py``.
    """
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
