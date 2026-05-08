from __future__ import annotations

import os
from pathlib import Path
from typing import Final

__all__ = [
    "is_desktop_mode",
    "desktop_data_dir",
    "desktop_workspaces_dir",
    "desktop_logs_dir",
    "backend_port",
    "DESKTOP_FLAG_ENV",
    "BACKEND_PORT_ENV",
]

DESKTOP_FLAG_ENV: Final[str] = "AILOUROS_DESKTOP"
DATA_DIR_ENV: Final[str] = "AILOUROS_DATA_DIR"
WORKSPACES_DIR_ENV: Final[str] = "AILOUROS_WORKSPACES_DIR"
LOGS_DIR_ENV: Final[str] = "AILOUROS_LOGS_DIR"
BACKEND_PORT_ENV: Final[str] = "AILOUROS_BACKEND_PORT"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def is_desktop_mode() -> bool:
    raw = os.getenv(DESKTOP_FLAG_ENV, "").strip().lower()
    return raw in _TRUTHY


def _path_from_env(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def desktop_data_dir() -> Path | None:
    return _path_from_env(DATA_DIR_ENV)


def desktop_workspaces_dir() -> Path | None:
    return _path_from_env(WORKSPACES_DIR_ENV)


def desktop_logs_dir() -> Path | None:
    return _path_from_env(LOGS_DIR_ENV)


def backend_port() -> int | None:
    raw = os.getenv(BACKEND_PORT_ENV, "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return port
