"""Singleton instances shared across the UI/REST layer.

Canonical location: backend/UI/REST/task_instance.py.
``orchestrator/task_instance.py`` is kept as a re-export shim for backward compatibility.
"""

from __future__ import annotations

from backend.App.paths import artifacts_root
from backend.App.tasks.infrastructure.task_store_redis import TaskStore

# Anchored to the app root (not the process CWD), so a server restart from
# a different working directory still reads and writes the same on-disk
# artifacts. See backend/App/paths.py for rationale.
ARTIFACTS_ROOT = artifacts_root()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

task_store = TaskStore()
