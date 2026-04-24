from __future__ import annotations

import os
from pathlib import Path


def _split_watch_paths(paths: str | list[str] | None) -> list[str]:
    if paths is None:
        return []
    if isinstance(paths, str):
        raw_parts = paths.replace("\n", ",").split(",")
    else:
        raw_parts = paths
    return [str(part).strip() for part in raw_parts if str(part).strip()]


def resolve_watch_paths(
    workspace_root: str,
    extra_paths: str | list[str] | None = None,
) -> list[str]:
    root = Path(workspace_root).resolve()
    requested = _split_watch_paths(extra_paths)
    if not requested:
        return [str(root)]

    paths: list[str] = []
    for p in requested:
        candidate = Path(p)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if candidate.exists() and str(candidate) not in paths:
            paths.append(str(candidate))
    return paths or [str(root)]


def set_background_agent_env(enabled: bool, watch_paths: list[str]) -> None:
    os.environ["SWARM_BACKGROUND_AGENT"] = "1" if enabled else "0"
    os.environ["SWARM_BACKGROUND_AGENT_WATCH_PATHS"] = ":".join(watch_paths)
