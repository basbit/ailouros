"""Observability endpoints: session tracing (R1.4) and session status (R1.1)."""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()


def _get_trace_collector() -> Any:
    """Lazy import to avoid circular deps at module load."""
    try:
        from backend.UI.REST.task_instance import task_store as _ts
        collector = getattr(_ts, "_trace_collector", None)
        if collector is None:
            from backend.App.orchestration.infrastructure.in_memory_trace_collector import (
                InMemoryTraceCollector,
            )
            return InMemoryTraceCollector()
        return collector
    except Exception:
        from backend.App.orchestration.infrastructure.in_memory_trace_collector import (
            InMemoryTraceCollector,
        )
        return InMemoryTraceCollector()


def _get_session_store() -> Any:
    try:
        from backend.UI.REST.task_instance import task_store as _ts
        store = getattr(_ts, "_session_store", None)
        if store is None:
            from backend.App.orchestration.infrastructure.in_memory_session_store import (
                InMemorySessionStore,
            )
            return InMemorySessionStore()
        return store
    except Exception:
        from backend.App.orchestration.infrastructure.in_memory_session_store import (
            InMemorySessionStore,
        )
        return InMemorySessionStore()


# ---------------------------------------------------------------------------
# R1.4 — Trace endpoints
# ---------------------------------------------------------------------------

@router.get("/v1/trace/{session_id}")
async def get_trace(session_id: str) -> JSONResponse:
    """Return the full trace session for a given session_id."""
    collector = _get_trace_collector()
    session = collector.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Trace session '{session_id}' not found")
    return JSONResponse(dataclasses.asdict(session))


# ---------------------------------------------------------------------------
# R1.1 — Session endpoints
# ---------------------------------------------------------------------------

@router.get("/v1/sessions/{task_id}")
async def list_sessions(task_id: str) -> JSONResponse:
    """List all durable sessions for a task_id."""
    store = _get_session_store()
    sessions = store.list_sessions(task_id)
    return JSONResponse([dataclasses.asdict(s) for s in sessions])


@router.get("/v1/sessions/{session_id}/status")
async def get_session_status(session_id: str) -> JSONResponse:
    """Return session status and latest checkpoint."""
    store = _get_session_store()
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    cp = store.get_latest_checkpoint(session_id)
    return JSONResponse({
        "session": dataclasses.asdict(session),
        "latest_checkpoint": dataclasses.asdict(cp) if cp else None,
    })
