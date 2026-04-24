from __future__ import annotations

import json
import logging
import os
import queue
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Optional

from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
    update_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import _pipeline_should_cancel
from backend.App.orchestration.domain.exceptions import PipelineCancelled

logger = logging.getLogger(__name__)


def _step_heartbeat_interval_sec() -> float:
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
    total = max(0, int(seconds))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class StepStreamExecutor:

    def run(
        self,
        step_id: str,
        step_func: Callable[[PipelineState], dict[str, Any]],
        state: Any,
    ) -> Generator[dict[str, Any], None, None]:
        pq: queue.Queue[str] = queue.Queue()
        set_ephemeral(state, "_stream_progress_queue", pq)
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
                update_ephemeral(state, delta)
        finally:
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception as exc:
                    logger.debug("pool.shutdown in finally failed: %s", exc)
            pop_ephemeral(state, "_stream_progress_queue")
