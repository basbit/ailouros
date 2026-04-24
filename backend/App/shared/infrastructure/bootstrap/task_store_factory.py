from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.App.paths import artifacts_root as _paths_artifacts_root

_task_store_singleton: Any = None
_artifacts_root_singleton: Path | None = None


def get_artifacts_root() -> Path:
    global _artifacts_root_singleton
    if _artifacts_root_singleton is None:
        root = _paths_artifacts_root()
        root.mkdir(parents=True, exist_ok=True)
        _artifacts_root_singleton = root
    return _artifacts_root_singleton


def get_task_store() -> Any:
    global _task_store_singleton
    if _task_store_singleton is None:
        from backend.App.tasks.infrastructure.task_store_redis import TaskStore
        _task_store_singleton = TaskStore()
    return _task_store_singleton
