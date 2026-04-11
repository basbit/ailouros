"""Application-layer use-case: fire a scheduled pipeline job.

The threading timer machinery (``_schedule_store``, ``_schedule_timers``,
``_schedule_lock``) remains in ``backend/UI/REST/controllers/schedules.py`` because
it is tightly coupled to the FastAPI lifespan and timer management.  Only the actual
pipeline-run logic (the ``_run()`` inner function inside ``_schedule_fire``)
is extracted here so it can be tested independently.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from backend.App.integrations.infrastructure.agent_registry import merge_agent_config

logger = logging.getLogger(__name__)


def fire_schedule_job(
    job_id: str,
    job: dict[str, Any],
    task_store: Any,
    run_pipeline_fn: Callable,
    *,
    on_success: Optional[Callable[[str, str], None]] = None,
) -> None:
    """Execute a scheduled pipeline job synchronously (intended to run in a background thread).

    Parameters
    ----------
    job_id:
        The schedule job identifier (used only for logging and the ``on_success`` callback).
    job:
        The job config dict (read from ``_schedule_store[job_id]``).
    task_store:
        The ``TaskStore`` instance used to create / update tasks.
    run_pipeline_fn:
        Callable with the same signature as ``pipeline.graph.run_pipeline``; injected so
        the function is easily testable without a real pipeline.
    on_success:
        Optional callback invoked after a successful run with ``(job_id, task_id)``.
        In production ``_schedule_fire`` uses this to update ``_schedule_store`` under the lock.
    """
    prompt = str(job.get("prompt") or "")
    agent_config_raw = job.get("agent_config") or {}
    pipeline_steps_raw = job.get("pipeline_steps")
    workspace_root = str(job.get("workspace_root") or "")
    workspace_write = bool(job.get("workspace_write", False))

    tid: Optional[str] = None
    try:
        ac = merge_agent_config(agent_config_raw)
        steps_list: Optional[list[str]] = None
        if isinstance(pipeline_steps_raw, list):
            steps_list = [str(s).strip() for s in pipeline_steps_raw if str(s).strip()]

        task = task_store.create_task(f"[scheduled:{job_id[:8]}] {prompt[:120]}")
        tid = task["task_id"]

        run_pipeline_fn(
            prompt,
            ac,
            steps_list,
            workspace_root,
            workspace_write,
            tid,
        )

        task_store.update_task(tid, status="completed")

        if on_success is not None:
            on_success(job_id, tid)

    except Exception as exc:
        logger.warning("scheduled job %s failed: %s", job_id, exc)
        if tid is not None:
            task_store.update_task(tid, status="failed", message=str(exc)[:2000])
        raise
