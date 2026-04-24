from __future__ import annotations

import os
from pathlib import Path

from backend.App.shared.infrastructure.app_config_load import load_app_config_json

_IGNORED_DIRECTORIES = frozenset(
    str(name)
    for name in load_app_config_json("workspace_ignored_dirs.json")["ignored_directory_names"]
)


def list_workspace_files(workspace_root: str, max_files: int = 2000) -> list[str]:
    root = Path(workspace_root).expanduser().resolve()
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _IGNORED_DIRECTORIES and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            relative_path = Path(dirpath).relative_to(root) / name
            files.append(relative_path.as_posix())
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break
    return files


__all__ = ["list_workspace_files"]
