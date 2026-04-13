"""StepStreamExecutor: run a pipeline step with queued progress events.

Extracted from pipeline_step_runner.py to separate the threading/progress-stream
infrastructure concern from output-key knowledge (which lives in
StepOutputExtractor).
"""
from __future__ import annotations

import logging
import queue
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import _pipeline_should_cancel
from backend.App.orchestration.domain.exceptions import PipelineCancelled

logger = logging.getLogger(__name__)


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
        state: PipelineState,
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
            while True:
                while True:
                    try:
                        msg = pq.get_nowait()
                        yield {"agent": step_id, "status": "progress", "message": msg}
                    except queue.Empty:
                        break
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
                state.update(delta)
        finally:
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception as exc:
                    logger.debug("pool.shutdown in finally failed: %s", exc)
            cast(dict, state).pop("_stream_progress_queue", None)
