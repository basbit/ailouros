"""Schedule routes: /v1/schedule/*

Owns the schedule globals (_schedule_store, _schedule_lock, _schedule_timers,
_schedule_fire). These are the canonical definitions; no orchestrator.app dependency.
"""

from __future__ import annotations

import asyncio
import threading
import uuid as _uuid
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.UI.REST.task_instance import task_store

router = APIRouter()

# ---------------------------------------------------------------------------
# Schedule globals
# ---------------------------------------------------------------------------

_schedule_store: dict[str, dict[str, Any]] = {}  # job_id -> job config
_schedule_lock = threading.Lock()
_schedule_timers: dict[str, threading.Timer] = {}


def _schedule_fire(job_id: str) -> None:
    """Run a scheduled task and reschedule the next run."""
    with _schedule_lock:
        job = _schedule_store.get(job_id)
    if not job or not job.get("enabled", True):
        return

    interval = int(job.get("interval_seconds") or 0)

    def _on_success(jid: str, tid: str) -> None:
        with _schedule_lock:
            if jid in _schedule_store:
                _schedule_store[jid]["last_run"] = _datetime.now(_timezone.utc).isoformat()
                _schedule_store[jid]["last_task_id"] = tid

    def _run() -> None:
        from backend.App.scheduling.application.fire_schedule_job_fn import fire_schedule_job as _fire
        from backend.App.orchestration.application.pipeline_graph import run_pipeline as _run_pipeline
        _fire(
            job_id,
            job,
            task_store,
            _run_pipeline,
            on_success=_on_success,
        )

    threading.Thread(target=_run, daemon=True, name=f"sched-{job_id[:8]}").start()

    # Reschedule
    if interval > 0:
        t = threading.Timer(interval, _schedule_fire, args=(job_id,))
        t.daemon = True
        with _schedule_lock:
            _schedule_timers[job_id] = t
        t.start()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScheduleCreateRequest(BaseModel):
    """POST /v1/schedule body — create or update a scheduled task."""

    name: str = ""
    prompt: str
    interval_seconds: int = 3600
    delay_seconds: int = 0
    agent_config: Optional[dict[str, Any]] = None
    pipeline_steps: Optional[list[str]] = None
    workspace_root: str = ""
    workspace_write: bool = False
    enabled: bool = True


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.post("/v1/schedule")
async def schedule_create(req: ScheduleCreateRequest) -> JSONResponse:
    """Create a scheduled task (CronJob-style)."""
    job_id = str(_uuid.uuid4())
    job: dict[str, Any] = {
        "id": job_id,
        "name": req.name or f"job-{job_id[:8]}",
        "prompt": req.prompt,
        "interval_seconds": req.interval_seconds,
        "agent_config": req.agent_config or {},
        "pipeline_steps": req.pipeline_steps,
        "workspace_root": req.workspace_root,
        "workspace_write": req.workspace_write,
        "enabled": req.enabled,
        "created_at": _datetime.now(_timezone.utc).isoformat(),
        "last_run": None,
        "last_task_id": None,
    }
    with _schedule_lock:
        _schedule_store[job_id] = job

    if req.enabled:
        delay = max(0, req.delay_seconds)
        t = threading.Timer(delay, _schedule_fire, args=(job_id,))
        t.daemon = True
        with _schedule_lock:
            _schedule_timers[job_id] = t
        t.start()

    return JSONResponse(content={"ok": True, "job_id": job_id, "job": job})


@router.get("/v1/schedule")
async def schedule_list() -> JSONResponse:
    """List all scheduled tasks."""
    with _schedule_lock:
        jobs = list(_schedule_store.values())
    return JSONResponse(content={"jobs": jobs})


@router.delete("/v1/schedule/{job_id}")
async def schedule_delete(job_id: str) -> JSONResponse:
    """Cancel and delete a scheduled task."""
    with _schedule_lock:
        if job_id not in _schedule_store:
            raise HTTPException(status_code=404, detail="Job not found")
        _schedule_store.pop(job_id, None)
        timer = _schedule_timers.pop(job_id, None)
    if timer is not None:
        timer.cancel()
    return JSONResponse(content={"ok": True, "deleted": job_id})


@router.patch("/v1/schedule/{job_id}")
async def schedule_update(job_id: str, body: Optional[dict] = None) -> JSONResponse:
    """Update job fields (enabled, interval_seconds, etc.)."""
    if body is None:
        body = {}
    if "interval_seconds" in body:
        try:
            iv = int(body["interval_seconds"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="interval_seconds must be an integer")
        if iv <= 0:
            raise HTTPException(status_code=422, detail="interval_seconds must be > 0")

    old_timer_to_cancel: Optional[threading.Timer] = None
    new_timer: Optional[threading.Timer] = None

    with _schedule_lock:
        if job_id not in _schedule_store:
            raise HTTPException(status_code=404, detail="Job not found")
        allowed = {"name", "interval_seconds", "enabled", "prompt", "workspace_root",
                   "workspace_write", "pipeline_steps"}
        for k, v in body.items():
            if k in allowed:
                _schedule_store[job_id][k] = v
        job = dict(_schedule_store[job_id])

        if body.get("enabled") is True:
            old_timer_to_cancel = _schedule_timers.pop(job_id, None)
            interval = max(1, int(job.get("interval_seconds") or 3600))
            new_timer = threading.Timer(interval, _schedule_fire, args=(job_id,))
            new_timer.daemon = True
            _schedule_timers[job_id] = new_timer
        elif body.get("enabled") is False:
            old_timer_to_cancel = _schedule_timers.pop(job_id, None)

    if old_timer_to_cancel is not None:
        old_timer_to_cancel.cancel()
    if new_timer is not None:
        new_timer.start()

    return JSONResponse(content={"ok": True, "job": job})


@router.post("/v1/schedule/{job_id}/run")
async def schedule_run_now(job_id: str) -> JSONResponse:
    """Run a scheduled task immediately (outside schedule)."""
    with _schedule_lock:
        if job_id not in _schedule_store:
            raise HTTPException(status_code=404, detail="Job not found")
    await asyncio.to_thread(_schedule_fire, job_id)
    return JSONResponse(content={"ok": True, "job_id": job_id, "triggered": True})
