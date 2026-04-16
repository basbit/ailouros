"""StepStreamExecutor: run a pipeline step with queued progress events.

Extracted from pipeline_step_runner.py to separate the threading/progress-stream
infrastructure concern from output-key knowledge (which lives in
StepOutputExtractor).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import _pipeline_should_cancel
from backend.App.orchestration.domain.exceptions import PipelineCancelled

logger = logging.getLogger(__name__)


def _step_heartbeat_interval_sec() -> float:
    """Seconds between synthetic heartbeat events when a step is idle.

    A step that blocks inside a long LLM call emits no progress events
    of its own; the heartbeat lets the UI show "still working, elapsed
    N s" instead of going silent. Configurable via
    ``SWARM_STEP_HEARTBEAT_SEC`` (default 15 s; set to 0 to disable).
    """
    raw = os.getenv("SWARM_STEP_HEARTBEAT_SEC", "").strip()
    if not raw:
        return 15.0
    try:
        val = float(raw)
    except ValueError:
        logger.warning("SWARM_STEP_HEARTBEAT_SEC=%r is not a number, using 15s", raw)
        return 15.0
    return max(0.0, val)


def _format_elapsed_wall(seconds: float) -> str:
    """Format elapsed wall-clock seconds into a human-readable string.

    Examples:
        45    → "45s"
        90    → "1m 30s"
        3661  → "1h 1m 1s"
        0     → "0s"
        -5    → "0s"
    """
    total = max(0, int(seconds))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class StepStreamExecutor:
    """Run a pipeline step in a worker thread and yield SSE progress events.

    Does not know about ``_AGENT_STATE_KEYS`` — it relies on the step function
    itself to return a delta dict that is merged into *state*.

    Usage::

        executor = StepStreamExecutor()
        for event in executor.run("dev", dev_node_fn, state):
            ...
    """

    def run(
        self,
        step_id: str,
        step_func: Callable[[PipelineState], dict[str, Any]],
        state: Any,
    ) -> Generator[dict[str, Any], None, None]:
        """Run *step_func* in a thread and yield queued progress events.

        The step function is executed in a ``ThreadPoolExecutor``.  Progress
        events written to the internal queue by ``_stream_progress_emit`` are
        yielded as they arrive.

        Args:
            step_id: The agent/step identifier used in yielded events.
            step_func: Callable that accepts *state* and returns a delta dict.
            state: Current mutable pipeline state.

        Raises:
            HumanApprovalRequired: re-raised from the step function.
            PipelineCancelled: raised when the cancel event is set mid-step.
            Exception: any other exception from *step_func* is re-raised.
        """
        pq: queue.Queue[str] = queue.Queue()
        cast(dict, state)["_stream_progress_queue"] = pq
        holder: dict[str, Any] = {}
        pool: Optional[ThreadPoolExecutor] = None
        try:

            def work() -> None:
                try:
                    holder["delta"] = step_func(state)
                except BaseException as exc:
                    holder["exc"] = exc

            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(work)
            _heartbeat_sec = _step_heartbeat_interval_sec()
            _start_ts = time.monotonic()
            _last_event_ts = _start_ts
            while True:
                any_event = False
                while True:
                    try:
                        msg = pq.get_nowait()
                        any_event = True
                        # Structured JSON events (e.g. output_truncated, auto_approved)
                        # are emitted with their own status type instead of "progress".
                        if msg.startswith('{"_event_type":'):
                            try:
                                evt = json.loads(msg)
                                evt_type = evt.pop("_event_type", "progress")
                                evt.setdefault("agent", step_id)
                                evt["status"] = evt_type
                                yield evt
                            except (json.JSONDecodeError, Exception):
                                yield {"agent": step_id, "status": "progress", "message": msg}
                        else:
                            yield {"agent": step_id, "status": "progress", "message": msg}
                    except queue.Empty:
                        break
                if any_event:
                    _last_event_ts = time.monotonic()
                # Heartbeat — emit "still working" when the step stays silent
                # for too long (e.g. blocked inside an LLM call). Bug aec02899
                # manifested as SSE going dark; heartbeat surfaces liveness.
                if _heartbeat_sec > 0 and not fut.done():
                    _now = time.monotonic()
                    if _now - _last_event_ts >= _heartbeat_sec:
                        elapsed = _now - _start_ts
                        yield {
                            "agent": step_id,
                            "status": "heartbeat",
                            "message": (
                                f"{step_id}: still working (elapsed "
                                f"{_format_elapsed_wall(elapsed)})"
                            ),
                            "elapsed_sec": round(elapsed, 1),
                        }
                        _last_event_ts = _now
                if _pipeline_should_cancel(state):
                    pool.shutdown(wait=False, cancel_futures=True)
                    pool = None
                    raise PipelineCancelled(
                        "pipeline cancelled (client disconnect or server shutdown)"
                    )
                if fut.done():
                    break
                wait((fut,), timeout=0.12)
            if pool is not None:
                pool.shutdown(wait=True)
                pool = None
            exc = holder.get("exc")
            if exc is not None:
                raise exc
            delta = holder.get("delta")
            if isinstance(delta, dict):
                cast(dict, state).update(delta)
        finally:
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception as exc:
                    logger.debug("pool.shutdown in finally failed: %s", exc)
            cast(dict, state).pop("_stream_progress_queue", None)
