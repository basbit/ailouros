from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

_state_lock = threading.Lock()  # module-level lock for merging deltas

_MAX_PARALLEL_WORKERS = int(os.getenv("SWARM_PARALLEL_MAX_WORKERS", "4"))


class ParallelExecutor:

    def __init__(
        self,
        state: dict[str, Any],
        *,
        progress_queue: Any = None,
    ) -> None:
        self._state = state
        self._progress_queue = progress_queue

    def run_parallel(
        self,
        steps: list[tuple[str, Callable]],
        *,
        timeout: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not steps:
            return {}
        if len(steps) == 1:
            step_id, fn = steps[0]
            try:
                delta = fn(self._state)
                if isinstance(delta, dict):
                    self._state.update(delta)
                return {step_id: delta or {}}
            except Exception as exc:
                logger.error("ParallelExecutor: step %r failed: %s", step_id, exc)
                raise

        import queue as _q
        import json as _json

        branch_queues: dict[str, _q.Queue] = {}
        for step_id, _ in steps:
            branch_queues[step_id] = _q.Queue()

        def _drain_progress() -> None:
            if not isinstance(self._progress_queue, _q.Queue):
                return
            for sid, bq in branch_queues.items():
                while True:
                    try:
                        msg = bq.get_nowait()
                        if msg.startswith('{"_event_type":'):
                            try:
                                evt = _json.loads(msg)
                                evt.setdefault("step_id", sid)
                                self._progress_queue.put(_json.dumps(evt))
                            except Exception:
                                self._progress_queue.put(msg)
                        else:
                            self._progress_queue.put(
                                _json.dumps({
                                    "_event_type": "progress",
                                    "step_id": sid,
                                    "message": msg,
                                })
                            )
                    except _q.Empty:
                        break

        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, Exception] = {}

        def _run_step(step_id: str, fn: Callable) -> tuple[str, dict[str, Any]]:
            branch_state = dict(self._state)
            branch_state["_stream_progress_queue"] = branch_queues[step_id]
            try:
                delta = fn(branch_state)
                return step_id, delta or {}
            except Exception as exc:
                logger.error("ParallelExecutor: step %r failed: %s", step_id, exc)
                raise

        workers = min(len(steps), _MAX_PARALLEL_WORKERS)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_run_step, step_id, fn): step_id
                for step_id, fn in steps
            }
            for fut in as_completed(futures, timeout=timeout):
                step_id = futures[fut]
                _drain_progress()
                try:
                    sid, delta = fut.result()
                    results[sid] = delta
                    if isinstance(delta, dict):
                        with _state_lock:
                            self._state.update(delta)
                    _emit_parallel_progress(
                        self._progress_queue, step_id, "completed",
                        f"[parallel] {step_id} completed"
                    )
                except Exception as exc:
                    errors[step_id] = exc
                    _emit_parallel_progress(
                        self._progress_queue, step_id, "error",
                        f"[parallel] {step_id} failed: {exc}"
                    )

        _drain_progress()

        if isinstance(self._progress_queue, _q.Queue):
            self._state["_stream_progress_queue"] = self._progress_queue

        if errors:
            raise RuntimeError(
                f"Parallel steps failed: {'; '.join(f'{k}: {v}' for k, v in errors.items())}"
            )

        logger.info("ParallelExecutor: completed %d steps in parallel", len(results))
        return results


def _emit_parallel_progress(pq: Any, step_id: str, event_type: str, message: str) -> None:
    import queue as _q
    import json as _json
    if not isinstance(pq, _q.Queue):
        return
    try:
        pq.put(_json.dumps({
            "_event_type": f"parallel_{event_type}",
            "step_id": step_id,
            "message": message,
        }))
    except Exception:
        pass
