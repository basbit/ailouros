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
# _run_step_with_stream_progress below.
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
    "_emit_completed",
    "_run_step_with_stream_progress",
    "_stream_progress_heartbeat_seconds",
]

# ---------------------------------------------------------------------------
# Legacy module-level functions kept for callers that import them directly.
# ---------------------------------------------------------------------------
import os as _os
import queue as _queue
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor as _TPE, wait as _fut_wait
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import PipelineCancelled


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


_default_executor = StepStreamExecutor()


def _emit_completed(agent: str, state: PipelineState) -> dict[str, Any]:
    """Completion event for a step: return primary text + model/provider if available.

    .. deprecated::
        Use :class:`StepOutputExtractor`.emit_completed() directly.
    """
    from backend.App.orchestration.application.step_output_extractor import (
        StepOutputExtractor as _Ext,
    )
    return _Ext().emit_completed(agent, state)


def _run_step_with_stream_progress(
    step_id: str,
    step_func: Callable[[PipelineState], dict[str, Any]],
    state: PipelineState,
) -> Generator[dict[str, Any], None, None]:
    """Run a pipeline step in a worker thread and yield SSE progress events.

    Uses the module-level reference to ``_pipeline_should_cancel`` so tests can
    patch it at this module level.

    .. deprecated::
        Use :class:`StepStreamExecutor`.run() directly.
    """
    import backend.App.orchestration.application.pipeline_step_runner as _self

    pq: _queue.Queue[str] = _queue.Queue()
    cast(dict, state)["_stream_progress_queue"] = pq
    holder: dict[str, Any] = {}
    pool: Optional[_TPE] = None
    try:

        def work() -> None:
            try:
                holder["delta"] = step_func(state)
            except BaseException as exc:
                holder["exc"] = exc

        pool = _TPE(max_workers=1)
        fut = pool.submit(work)
        while True:
            while True:
                try:
                    msg = pq.get_nowait()
                    yield {"agent": step_id, "status": "progress", "message": msg}
                except _queue.Empty:
                    break
            if _self._pipeline_should_cancel(state):
                pool.shutdown(wait=False, cancel_futures=True)
                pool = None
                raise PipelineCancelled(
                    "pipeline cancelled (client disconnect or server shutdown)"
                )
            if fut.done():
                break
            _fut_wait((fut,), timeout=0.12)
        if pool is not None:
            pool.shutdown(wait=True)
            pool = None
        exc = holder.get("exc")
        if exc is not None:
            raise exc
        delta = holder.get("delta")
        if isinstance(delta, dict):
            state.update(delta)
    finally:
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                import logging as _logging
                _logging.getLogger(__name__).debug("pool.shutdown in finally failed: %s", exc)
        cast(dict, state).pop("_stream_progress_queue", None)
