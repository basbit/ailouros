from __future__ import annotations

from backend.App.shared.infrastructure.bootstrap.task_store_factory import get_artifacts_root, get_task_store

ARTIFACTS_ROOT = get_artifacts_root()
task_store = get_task_store()
