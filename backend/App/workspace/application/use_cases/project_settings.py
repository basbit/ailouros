from __future__ import annotations

from typing import Any

from backend.App.workspace.infrastructure.project_settings import (
    load_project_settings,
    project_settings_path,
    save_project_settings,
)


def get_project_settings_payload(workspace_root: str) -> dict[str, Any]:
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must not be empty")
    settings = load_project_settings(workspace_root)
    path = project_settings_path(workspace_root)
    return {
        "exists": settings is not None,
        "path": str(path),
        "settings": settings,
    }


def save_project_settings_payload(
    workspace_root: str, settings: dict[str, Any]
) -> dict[str, Any]:
    if not workspace_root or not workspace_root.strip():
        raise ValueError("workspace_root must not be empty")
    save_project_settings(workspace_root, settings)
    path = project_settings_path(workspace_root)
    return {
        "exists": True,
        "path": str(path),
        "settings": settings,
    }
