from __future__ import annotations

from backend.App.orchestration.application.pipeline.step_output_extractor import (
    _AGENT_STATE_KEYS,
    StepOutput,
    StepOutputExtractor,
    final_pipeline_user_message,
    primary_output_for_step,
    task_store_agent_label,
)

from backend.App.orchestration.infrastructure.step_stream_executor import (
    StepStreamExecutor,
    _format_elapsed_wall,
)

from backend.App.orchestration.application.nodes._shared import (
    _pipeline_should_cancel,
)

__all__ = [
    "_AGENT_STATE_KEYS",
    "StepOutput",
    "StepOutputExtractor",
    "final_pipeline_user_message",
    "primary_output_for_step",
    "task_store_agent_label",
    "StepStreamExecutor",
    "_format_elapsed_wall",
    "_pipeline_should_cancel",
    "_stream_progress_heartbeat_seconds",
]

import os as _os


def _stream_progress_heartbeat_seconds() -> float:
    _default = 8.0
    _min = 2.0
    _max = 120.0
    raw = _os.environ.get("SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC", "")
    if not raw:
        return _default
    try:
        value = float(raw)
    except ValueError:
        return _default
    return max(_min, min(_max, value))
