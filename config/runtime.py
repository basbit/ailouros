"""Canonical app-level runtime configuration.

This module is the single source of truth for env-backed defaults and for
resolving shipped config files under ``app/config``.
"""
from __future__ import annotations

import os
from pathlib import Path

_APP_ROOT: Path = Path(__file__).resolve().parent.parent
_CONFIG_ROOT: Path = _APP_ROOT / "config"

_OLLAMA_DEFAULT_URL: str = "http://localhost:11434/v1"
_LMSTUDIO_DEFAULT_URL: str = "http://localhost:1234/v1"
_CLOUD_MODEL_DEFAULT: str = "claude-3-5-sonnet-latest"
_REDIS_URL_DEFAULT: str = "redis://localhost:6379/0"

OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", _OLLAMA_DEFAULT_URL).strip()
LMSTUDIO_BASE_URL: str = os.getenv("LMSTUDIO_BASE_URL", _LMSTUDIO_DEFAULT_URL).strip()
SWARM_MODEL_CLOUD_DEFAULT: str = os.getenv("SWARM_MODEL_CLOUD_DEFAULT", _CLOUD_MODEL_DEFAULT).strip()
REDIS_URL: str = os.getenv("REDIS_URL", _REDIS_URL_DEFAULT).strip()


def app_root() -> Path:
    """Return the runtime app root.

    ``SWARM_PROJECT_ROOT`` keeps its existing meaning: when set, relative config
    paths resolve against that root instead of the in-repo ``app/`` directory.
    """
    override = os.getenv("SWARM_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _APP_ROOT


def app_config_root() -> Path:
    """Return the canonical config directory root."""
    return app_root() / "config"


def resolve_app_config_path(relative_path: str, *, env_var: str = "") -> Path:
    """Resolve a shipped config path under ``app/config``.

    When *env_var* is provided and set, its value wins. Relative override paths
    are resolved from :func:`app_root`.
    """
    if env_var:
        override = os.getenv(env_var, "").strip()
        if override:
            path = Path(override).expanduser()
            if not path.is_absolute():
                path = app_root() / path
            return path
    return app_config_root() / relative_path


__all__ = [
    "app_root",
    "app_config_root",
    "resolve_app_config_path",
    "OLLAMA_BASE_URL",
    "LMSTUDIO_BASE_URL",
    "SWARM_MODEL_CLOUD_DEFAULT",
    "REDIS_URL",
]
