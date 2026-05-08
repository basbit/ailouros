from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _is_safe_descendant(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _platform_open_command() -> list[str] | None:
    system = platform.system().lower()
    if system == "darwin":
        opener = shutil.which("open")
        return [opener] if opener else None
    if system == "linux":
        opener = shutil.which("xdg-open")
        return [opener] if opener else None
    if system == "windows":
        return ["cmd", "/c", "start", ""]
    return None


def reveal(target_path: Path, root: Path) -> dict[str, Any]:
    if not _is_safe_descendant(target_path, root):
        return {
            "ok": False,
            "platform": platform.system().lower(),
            "absolute_path": "",
            "reason": "path_outside_artifacts_root",
        }
    absolute = target_path.resolve()
    if not absolute.exists():
        return {
            "ok": False,
            "platform": platform.system().lower(),
            "absolute_path": str(absolute),
            "reason": "path_not_found",
        }
    if (os.getenv("SWARM_DISABLE_OS_REVEAL") or "").strip() in {"1", "true", "yes", "on"}:
        return {
            "ok": False,
            "platform": platform.system().lower(),
            "absolute_path": str(absolute),
            "reason": "reveal_disabled_by_env",
        }
    command = _platform_open_command()
    if command is None:
        return {
            "ok": False,
            "platform": platform.system().lower(),
            "absolute_path": str(absolute),
            "reason": "no_supported_opener",
        }
    try:
        subprocess.Popen([*command, str(absolute)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        logger.warning("artifact_reveal: open failed for %s: %s", absolute, exc)
        return {
            "ok": False,
            "platform": platform.system().lower(),
            "absolute_path": str(absolute),
            "reason": str(exc),
        }
    return {
        "ok": True,
        "platform": platform.system().lower(),
        "absolute_path": str(absolute),
    }
