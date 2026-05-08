from __future__ import annotations

import os

_OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"
_LMSTUDIO_DEFAULT_URL = "http://localhost:1234/v1"

OLLAMA_BASE_URL: str = (
    os.getenv("OLLAMA_BASE_URL") or _OLLAMA_DEFAULT_URL
).strip()

LMSTUDIO_BASE_URL: str = (
    os.getenv("LMSTUDIO_BASE_URL") or _LMSTUDIO_DEFAULT_URL
).strip()

ANTHROPIC_MAX_TOKENS: int = 2048
try:
    _raw_mt = os.getenv("ANTHROPIC_MAX_TOKENS", "").strip()
    if _raw_mt:
        ANTHROPIC_MAX_TOKENS = int(_raw_mt)
except ValueError:
    pass

SWARM_MODEL_CLOUD_DEFAULT: str = (
    os.getenv("SWARM_MODEL_CLOUD", "claude-3-5-sonnet-latest") or "claude-3-5-sonnet-latest"
).strip()
