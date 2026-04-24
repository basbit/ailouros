
from __future__ import annotations

import os


def swarm_max_parallel_tasks() -> int:
    try:
        v = int(os.getenv("SWARM_MAX_PARALLEL_TASKS", "4"))
    except ValueError:
        v = 4
    return max(1, v)
