"""Agent-layer configuration constants.

Shared by ``agents.*`` modules.  ``orchestrator.config`` re-exports these so
that orchestrator modules can import from a single place.

.. warning:: **Import-time evaluation — test isolation caveat**

    All constants in this module are evaluated *once*, when the module is first
    imported.  ``monkeypatch.setenv(...)`` called *after* the import has no
    effect on these values because Python caches the module object.

    In tests, patch the constants directly instead of the environment variable::

        monkeypatch.setattr("agents.config.OLLAMA_BASE_URL", "http://fake/v1")
        monkeypatch.setattr("agents.config.LMSTUDIO_BASE_URL", "http://fake/v1")
        monkeypatch.setattr("agents.config.SWARM_MODEL_CLOUD_DEFAULT", "test-model")

    Alternatively, ensure ``importlib.reload(agents.config)`` is called inside
    the test *after* setting the environment variable, though direct patching
    of the constants is simpler and more reliable.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Local LLM backends
# ---------------------------------------------------------------------------

_OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"
_LMSTUDIO_DEFAULT_URL = "http://localhost:1234/v1"

OLLAMA_BASE_URL: str = (
    os.getenv("OPENAI_BASE_URL") or os.getenv("OLLAMA_BASE_URL") or _OLLAMA_DEFAULT_URL
).strip()

LMSTUDIO_BASE_URL: str = (
    os.getenv("LMSTUDIO_BASE_URL") or _LMSTUDIO_DEFAULT_URL
).strip()

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

ANTHROPIC_MAX_TOKENS: int = 2048
try:
    _raw_mt = os.getenv("ANTHROPIC_MAX_TOKENS", "").strip()
    if _raw_mt:
        ANTHROPIC_MAX_TOKENS = int(_raw_mt)
except ValueError:
    pass

# ---------------------------------------------------------------------------
# Default cloud model (fallback when no per-role override is set)
# ---------------------------------------------------------------------------

SWARM_MODEL_CLOUD_DEFAULT: str = (
    os.getenv("SWARM_MODEL_CLOUD", "claude-3-5-sonnet-latest") or "claude-3-5-sonnet-latest"
).strip()
