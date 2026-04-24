from __future__ import annotations

from pathlib import Path

from backend.App.shared.application.settings_resolver import get_setting
from backend.App.shared.domain.validators import is_under

__all__ = ["validate_workspace_root"]


def validate_workspace_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"workspace_root is not a directory: {resolved}")

    base_raw: str = get_setting(
        "workspace.base",
        env_key="SWARM_WORKSPACE_BASE",
        default="",
    )
    if base_raw and base_raw.strip():
        base = Path(base_raw.strip()).expanduser().resolve()
        if not base.is_dir():
            raise ValueError(
                f"workspace.base (SWARM_WORKSPACE_BASE={base_raw!r}) is not a directory: {base}"
            )
        if not is_under(base, resolved):
            raise ValueError(
                f"workspace_root must be inside workspace.base ({base})"
            )
    return resolved
