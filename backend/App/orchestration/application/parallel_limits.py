"""Общие лимиты параллелизма для Dev/QA и MoA (env SWARM_MAX_PARALLEL_TASKS)."""

from __future__ import annotations

import os


def swarm_max_parallel_tasks() -> int:
    """Минимум 1: иначе Semaphore(0) и ThreadPoolExecutor(max_workers=0) ломают пайплайн."""
    try:
        v = int(os.getenv("SWARM_MAX_PARALLEL_TASKS", "4"))
    except ValueError:
        v = 4
    return max(1, v)
