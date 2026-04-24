from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.App.orchestration.application.agents.background_agent_control import (
    resolve_watch_paths,
    set_background_agent_env,
)
from backend.App.orchestration.application.use_cases.task_queries import (
    clarify_questions_payload,
    task_metrics_payload,
)
from backend.App.tasks.domain.ports import TaskId
from backend.UI.REST.schemas import BackgroundAgentRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _stop_background_agent(request: Request) -> None:
    agent = getattr(request.app.state, "background_agent", None)
    if agent is not None:
        try:
            agent.stop()
        finally:
            request.app.state.background_agent = None


def _get_task_store(request: Request):

    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise RuntimeError(
            "task_store not initialized on app.state. "
            "Ensure lifespan startup completed successfully."
        )
    return store


def _get_cancel_fn(request: Request):

    cancel_fn = getattr(request.app.state, "cancel_fn", None)
    if cancel_fn is None:
        raise RuntimeError(
            "cancel_fn not initialized on app.state. "
            "Ensure lifespan startup completed successfully."
        )
    return cancel_fn


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> JSONResponse:

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

    store = _get_task_store(request)
    try:
        task_data = store.get_task(task_id)
    except KeyError:
        return JSONResponse({"questions": []}, status_code=404)

    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    return JSONResponse(clarify_questions_payload(task_id, task_data, ARTIFACTS_ROOT))


@router.get("/v1/tasks/{task_id}/metrics")
def get_task_metrics(task_id: str) -> JSONResponse:

    return JSONResponse(task_metrics_payload(task_id))


@router.get("/v1/background-recommendations")
def get_background_recommendations(request: Request) -> JSONResponse:

    agent = getattr(request.app.state, "background_agent", None)
    if agent is None or not getattr(agent, "active", False):
        return JSONResponse({"active": False, "recommendations": []})
    recs = agent.drain_recommendations()
    return JSONResponse(
        {
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
        }
    )


@router.put("/v1/background-agent")
def configure_background_agent(
    body: BackgroundAgentRequest,
    request: Request,
) -> JSONResponse:
    from backend.App.orchestration.application.agents.background_agent import (
        BackgroundAgent,
    )

    try:
        if not body.enabled:
            _stop_background_agent(request)
            set_background_agent_env(False, [])
            return JSONResponse({"active": False, "watch_paths": []})

        workspace_root = body.workspace_root.strip()
        if not workspace_root:
            raise ValueError(
                "workspace_root is required when background agent is enabled"
            )
        watch_paths = resolve_watch_paths(workspace_root, body.watch_paths)
        _stop_background_agent(request)
        set_background_agent_env(True, watch_paths)
        agent = BackgroundAgent(
            watch_paths=watch_paths,
            enabled=True,
            environment=body.environment,
            model=body.model,
            remote_provider=body.remote_provider,
            remote_api_key=body.remote_api_key,
            remote_base_url=body.remote_base_url,
        )
        agent.start()
        request.app.state.background_agent = agent
        return JSONResponse({"active": agent.active, "watch_paths": watch_paths})
    except (OSError, ValueError) as exc:
        logger.warning("configure_background_agent failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)
