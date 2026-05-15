from __future__ import annotations

import re
from pathlib import Path

from backend.App.shared.application.desktop_mode import (
    desktop_workspaces_dir,
    is_desktop_mode,
)
from backend.App.shared.domain.validators import is_under

PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def desktop_info_payload() -> dict[str, object]:
    if not is_desktop_mode():
        return {"is_desktop": False, "workspaces_dir": None}
    base = desktop_workspaces_dir()
    if base is None:
        return {"is_desktop": True, "workspaces_dir": None}
    return {"is_desktop": True, "workspaces_dir": str(base.resolve())}


def init_desktop_project_workspace(project_id: str) -> Path:
    if not is_desktop_mode():
        raise ValueError("desktop mode is not active")
    base = desktop_workspaces_dir()
    if base is None:
        raise ValueError("AILOUROS_WORKSPACES_DIR is not set")
    if not PROJECT_ID_PATTERN.match(project_id or ""):
        raise ValueError(
            "project_id must be 1-64 chars of [A-Za-z0-9._-] and start with a letter or digit"
        )
    base_resolved = base.resolve()
    if not base_resolved.is_dir():
        raise ValueError(f"workspaces directory does not exist: {base_resolved}")
    target = (base_resolved / project_id).resolve()
    if not is_under(base_resolved, target):
        raise ValueError(f"project workspace escapes workspaces directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target
