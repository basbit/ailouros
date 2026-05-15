from __future__ import annotations

import os

from backend.App.orchestration.domain.pipeline_machine import (
    DEFAULT_MAX_ATTEMPTS_PER_DEFECT,
    DEFAULT_MAX_FIX_CYCLES,
    PipelineMachine,
)


def _env_int(variable: str, default: int) -> int:
    raw = os.getenv(variable)
    if raw is None:
        return default
    return int(raw)


def make_pipeline_machine() -> PipelineMachine:
    return PipelineMachine(
        max_fix_cycles=_env_int("SWARM_MAX_FIX_CYCLES", DEFAULT_MAX_FIX_CYCLES),
        max_attempts_per_defect=_env_int(
            "SWARM_MAX_ATTEMPTS_PER_DEFECT",
            DEFAULT_MAX_ATTEMPTS_PER_DEFECT,
        ),
    )
