"""Thin UI/REST controllers for task operations (H-6).

Each handler: parse → call use-case → format response.
No direct Redis/FS/subprocess calls here (INV-7).
Partial state for resume/retry is loaded from pipeline artifacts on disk
by helper functions defined below.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.App.tasks.domain.ports import TaskId
from backend.App.orchestration.infrastructure.pipeline_artifact_reader import (
    load_partial_pipeline_state,
    load_failed_step,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: resolve task store and cancel fn from app state or legacy singleton
# ---------------------------------------------------------------------------

def _get_task_store(request: Request):
    """Return task store from app.state (initialized in lifespan)."""
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise RuntimeError(
            "task_store not initialized on app.state. "
            "Ensure lifespan startup completed successfully."
        )
    return store


def _get_cancel_fn(request: Request):
    """Return cancel fn from app.state (initialized in lifespan)."""
    cancel_fn = getattr(request.app.state, "cancel_fn", None)
    if cancel_fn is None:
        raise RuntimeError(
            "cancel_fn not initialized on app.state. "
            "Ensure lifespan startup completed successfully."
        )
    return cancel_fn


# ---------------------------------------------------------------------------
# Deprecated local helpers — delegates kept for any remaining internal uses
# ---------------------------------------------------------------------------

def _load_partial_state(task_id: str) -> dict[str, Any]:
    """Deprecated: use load_partial_pipeline_state from pipeline_artifact_reader."""
    return load_partial_pipeline_state(task_id)


def _load_failed_step(task_id: str) -> str:
    """Deprecated: use load_failed_step from pipeline_artifact_reader."""
    return load_failed_step(task_id)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> JSONResponse:
    """Return task payload for legacy UI clients."""
    store = _get_task_store(request)
    try:
        payload = store.get_task(task_id)
    except KeyError:
        return JSONResponse({"detail": "task not found"}, status_code=404)
    return JSONResponse(content=payload, headers={"X-Task-Id": task_id})


@router.post("/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request) -> JSONResponse:
    from backend.App.orchestration.application.use_cases.cancel_task import (
        CancelTaskCommand,
        CancelTaskUseCase,
    )

    store = _get_task_store(request)
    cancel_fn = _get_cancel_fn(request)
    use_case = CancelTaskUseCase(task_store=store, cancel_event_fn=cancel_fn)

    # CancelTaskUseCase.execute expects TaskStorePort — legacy store satisfies duck-typing
    # but also supports str-based get/update (legacy interface).
    # Wrap task_id as TaskId for the use-case.
    result = use_case.execute(CancelTaskCommand(task_id=TaskId(task_id)))
    return JSONResponse(
        content={
            "task_id": task_id,
            "status": result.status.value,
            "was_active": result.was_active,
        },
        headers={"X-Task-Id": task_id},
    )


@router.get("/v1/tasks/{task_id}/clarify-questions")
def get_clarify_questions(task_id: str, request: Request) -> JSONResponse:
    """Return structured clarifying questions from the last clarify_input step."""
    from backend.App.orchestration.application.nodes.clarify_parser import parse_clarify_questions

    store = _get_task_store(request)
    try:
        task_data = store.get_task(task_id)
    except KeyError:
        return JSONResponse({"questions": []}, status_code=404)

    clarify_output = ""
    # 1. Try partial_state.clarify_input_output from pipeline.json
    partial = _load_partial_state(task_id)
    clarify_output = str(partial.get("clarify_input_output") or "")

    if not clarify_output:
        # 2. Check top-level "error" field of pipeline.json — clarify_input_node
        #    writes the output directly to state (a copy inside _hook_wrap), so
        #    _state_snapshot may miss it; but stream_handlers always stores
        #    str(exc) = clarify_output in pipeline_snapshot["error"].
        from backend.UI.REST.task_instance import ARTIFACTS_ROOT as _ARTIFACTS_ROOT
        try:
            _pj = _ARTIFACTS_ROOT / task_id / "pipeline.json"
            if _pj.is_file():
                _pj_data = json.loads(_pj.read_text())
                _err = str(_pj_data.get("error") or "")
                if "NEEDS_CLARIFICATION" in _err:
                    clarify_output = _err
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("get_clarify_questions: could not read pipeline.json for task %s: %s", task_id, exc)

    if not clarify_output:
        # 3. Fallback: scan task history for NEEDS_CLARIFICATION message
        history = task_data.get("history") or []
        for item in reversed(history):
            msg = str(item.get("message") or "")
            if "NEEDS_CLARIFICATION" in msg:
                clarify_output = msg
                break

    if not clarify_output:
        return JSONResponse({"questions": []})

    questions = parse_clarify_questions(clarify_output)
    return JSONResponse({
        "questions": [
            {"index": q.index, "text": q.text, "options": q.options}
            for q in questions
        ]
    })


@router.get("/v1/tasks/{task_id}/metrics")
def get_task_metrics(task_id: str) -> JSONResponse:
    """Return per-step token and timing metrics for a specific task.

    Uses ``snapshot_for_task`` from step_metrics to retrieve in-process
    counters accumulated during pipeline execution.  The response is shaped
    for the StepTokensPanel frontend component.
    """
    from backend.App.integrations.infrastructure.observability.step_metrics import (
        snapshot_for_task,
    )

    data = snapshot_for_task(task_id)
    raw_steps: dict = data.get("steps", {})

    steps = []
    for step_id, info in raw_steps.items():
        tokens: dict = info.get("tokens", {})
        steps.append({
            "step_id": step_id,
            "count": info.get("count", 0),
            "p50_ms": info.get("p50_ms", 0.0),
            "input_tokens": tokens.get("input_tokens", 0),
            "output_tokens": tokens.get("output_tokens", 0),
            "tool_calls_count": tokens.get("tool_calls_count", 0),
        })

    return JSONResponse({"steps": steps})


@router.get("/v1/background-recommendations")
def get_background_recommendations(request: Request) -> JSONResponse:
    """Drain and return pending background agent recommendations.

    Returns ``active: false`` when the background agent is disabled.
    Drains the recommendation queue — each item is returned only once.
    """
    agent = getattr(request.app.state, "background_agent", None)
    if agent is None:
        return JSONResponse({"active": False, "recommendations": []})
    recs = agent.drain_recommendations()
    return JSONResponse({
        "active": True,
        "recommendations": [
            {
                "event_type": r.event_type,
                "path": r.path,
                "message": r.message,
                "severity": r.severity,
                "suggested_action": r.suggested_action,
                "timestamp": r.timestamp,
            }
            for r in recs
        ],
    })
