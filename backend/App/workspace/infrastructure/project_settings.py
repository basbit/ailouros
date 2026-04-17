"""Project-scoped UI settings stored inside ``<workspace>/.swarm/settings.json``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from backend.App.workspace.infrastructure.workspace_io import validate_workspace_root

_SWARM_DIR = ".swarm"
_SETTINGS_FILE = "settings.json"


def _is_under(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def project_settings_path(workspace_root: str | Path) -> Path:
    """Return the canonical project settings path for a validated workspace."""
    root = validate_workspace_root(Path(workspace_root))
    candidate = (root / _SWARM_DIR / _SETTINGS_FILE).resolve(strict=False)
    if not _is_under(root, candidate):
        raise ValueError(
            f"project settings path escapes workspace root: {candidate}"
        )
    return candidate


def load_project_settings(workspace_root: str | Path) -> dict[str, Any] | None:
    """Load project settings JSON or return ``None`` when the file does not exist."""
    path = project_settings_path(workspace_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project settings must be a JSON object")
    return data


def save_project_settings(
    workspace_root: str | Path,
    settings: Mapping[str, Any],
) -> Path:
    """Persist project settings atomically and return the path written."""
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be a JSON object")
    path = project_settings_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    payload = json.dumps(dict(settings), indent=2, ensure_ascii=False) + "\n"
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)
    return path
