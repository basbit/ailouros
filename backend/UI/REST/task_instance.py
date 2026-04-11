"""Singleton instances shared across the UI/REST layer.

Canonical location: backend/UI/REST/task_instance.py.
``orchestrator/task_instance.py`` is kept as a re-export shim for backward compatibility.
"""

from __future__ import annotations

import os
from pathlib import Path

from backend.App.tasks.infrastructure.task_store_redis import TaskStore

ARTIFACTS_ROOT = Path(os.getenv("SWARM_ARTIFACTS_DIR", "var/artifacts")).resolve()
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

task_store = TaskStore()
