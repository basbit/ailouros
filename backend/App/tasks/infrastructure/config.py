"""Infrastructure configuration for the tasks bounded context.

Canonical location: backend/App/tasks/infrastructure/config.py.
``orchestrator/config.py`` is kept as a re-export shim for backward compatibility.
"""

from __future__ import annotations

import os

# Re-export agent-layer constants so existing imports keep working
from backend.App.integrations.infrastructure.llm.config import (
    ANTHROPIC_MAX_TOKENS,
    LMSTUDIO_BASE_URL,
    OLLAMA_BASE_URL,
    SWARM_MODEL_CLOUD_DEFAULT,
)

__all__ = [
    "ANTHROPIC_MAX_TOKENS",
    "LMSTUDIO_BASE_URL",
    "OLLAMA_BASE_URL",
    "SWARM_MODEL_CLOUD_DEFAULT",
    "REDIS_URL",
]

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

REDIS_URL: str = (
    os.getenv("REDIS_URL", "redis://localhost:6379/0") or "redis://localhost:6379/0"
).strip()
