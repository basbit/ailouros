"""Backward-compatibility shim for pipeline step execution helpers.

All functionality has been split into:
- ``step_output_extractor.py`` — StepOutputExtractor, StepOutput, _AGENT_STATE_KEYS
- ``backend.App.orchestration.infrastructure.step_stream_executor`` — StepStreamExecutor

Public names are re-exported here so existing imports remain unmodified.
"""
from __future__ import annotations

# Re-export output extractor components
from backend.App.orchestration.application.step_output_extractor import (
    _AGENT_STATE_KEYS,
    StepOutput,
    StepOutputExtractor,
    final_pipeline_user_message,
    primary_output_for_step,
    task_store_agent_label,
)

# Re-export infrastructure executor
from backend.App.orchestration.infrastructure.step_stream_executor import (
    StepStreamExecutor,
    _format_elapsed_wall,
)

# Re-export helper that tests patch at this module level.
# This comes from _shared; having it as a module-level name here means patching
# ``pipeline_step_runner._pipeline_should_cancel`` intercepts the calls inside
# StepStreamExecutor.run().
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

# ---------------------------------------------------------------------------
# Legacy module-level functions kept for callers that import them directly.
# ---------------------------------------------------------------------------
import os as _os


def _stream_progress_heartbeat_seconds() -> float:
    """Return the configured heartbeat interval for pipeline progress events.

    Reads ``SWARM_PIPELINE_PROGRESS_HEARTBEAT_SEC`` from the environment.
    Defaults to 8.0 seconds, clamped to [2.0, 120.0].
    """
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
