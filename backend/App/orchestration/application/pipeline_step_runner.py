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

# Re-export helpers that tests patch at this module level.
# These come from _shared and step_stream_executor; having them as module-level
# names here means patching ``pipeline_step_runner._pipeline_should_cancel`` or
# ``pipeline_step_runner._stream_progress_heartbeat_seconds`` intercepts the calls
# inside _run_step_with_stream_progress below.
from backend.App.orchestration.application.nodes._shared import (
    _pipeline_should_cancel,
)
from backend.App.orchestration.infrastructure.step_stream_executor import (
    _stream_progress_heartbeat_seconds,
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
    "_emit_completed",
    "_run_step_with_stream_progress",
]

# ---------------------------------------------------------------------------
# Legacy module-level functions kept for callers that import them directly.
# ---------------------------------------------------------------------------
import queue as _queue
import time as _time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor as _TPE, wait as _fut_wait
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import PipelineCancelled

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
    """Run a pipeline step in a worker thread and yield SSE progress + heartbeat events.

    Uses module-level references to ``_pipeline_should_cancel`` and
    ``_stream_progress_heartbeat_seconds`` so that tests can patch them at
    this module level.

    .. deprecated::
        Use :class:`StepStreamExecutor`.run() directly.
    """
    import backend.App.orchestration.application.pipeline_step_runner as _self

    pq: _queue.Queue[str] = _queue.Queue()
    cast(dict, state)["_stream_progress_queue"] = pq
    holder: dict[str, Any] = {}
    hb_every = _self._stream_progress_heartbeat_seconds()
    step_started = _time.monotonic()
    pool: Optional[_TPE] = None
    try:

        def work() -> None:
            try:
                holder["delta"] = step_func(state)
            except BaseException as exc:
                holder["exc"] = exc

        pool = _TPE(max_workers=1)
        fut = pool.submit(work)
        last_hb = _time.monotonic()
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
            now = _time.monotonic()
            if now - last_hb >= hb_every:
                last_hb = now
                elapsed = now - step_started
                # SLA warning: emit a distinct event if step exceeds the configured threshold.
                # SWARM_STEP_SLA_SEC_PLANNING (default 90) for planning steps,
                # SWARM_STEP_SLA_SEC_BUILD (default 300) for build steps.
                import os as _os
                _is_planning = step_id in (
                    "clarify_input", "pm", "review_pm", "ba", "review_ba",
                    "architect", "review_arch", "spec_merge", "review_spec",
                )
                _sla_env = "SWARM_STEP_SLA_SEC_PLANNING" if _is_planning else "SWARM_STEP_SLA_SEC_BUILD"
                _sla_default = "90" if _is_planning else "300"
                try:
                    _sla_sec = float(_os.getenv(_sla_env, _sla_default) or _sla_default)
                except ValueError:
                    _sla_sec = 90.0 if _is_planning else 300.0
                _sla_exceeded = _sla_sec > 0 and elapsed > _sla_sec
                _status = "warning" if _sla_exceeded else "progress"
                _sla_note = f" [SLA EXCEEDED: {_sla_env}={int(_sla_sec)}s]" if _sla_exceeded else ""
                yield {
                    "agent": step_id,
                    "status": _status,
                    "message": (
                        f"{step_id}: worker busy — building prompt or waiting for HTTP/LLM generation "
                        f"(elapsed {_format_elapsed_wall(elapsed)}; heartbeat every {int(hb_every)}s){_sla_note}…"
                    ),
                }
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
