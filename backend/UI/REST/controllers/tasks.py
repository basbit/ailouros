from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.App.orchestration.application.agents.background_agent_control import (
    resolve_watch_paths,
    set_background_agent_env,
)
from backend.App.orchestration.application.use_cases.task_queries import (
    clarify_questions_payload,
    runtime_telemetry_payload,
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
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    store = _get_task_store(request)
    try:
        payload = store.get_task(task_id)
    except KeyError:
        return JSONResponse({"detail": "task not found"}, status_code=404)
    enriched = dict(payload)
    enriched.update(runtime_telemetry_payload(task_id, ARTIFACTS_ROOT))
    return JSONResponse(content=enriched, headers={"X-Task-Id": task_id})


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


def _load_pipeline_snapshot(task_dir: Path) -> dict[str, Any]:
    pipeline_path = task_dir / "pipeline.json"
    if not pipeline_path.is_file():
        raise FileNotFoundError("pipeline.json not found")
    try:
        data = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"pipeline.json is not readable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("pipeline.json is not a JSON object")
    return data


@router.get("/v1/tasks/{task_id}/scenario-artifacts")
def get_scenario_artifacts(task_id: str) -> JSONResponse:
    from backend.App.orchestration.application.scenarios.artifact_check import (
        check_scenario_artifacts,
        summarize_artifact_status,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    task_dir = ARTIFACTS_ROOT / task_id
    try:
        snapshot = _load_pipeline_snapshot(task_dir)
    except FileNotFoundError:
        return JSONResponse({"detail": "task pipeline.json not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)

    scenario_id = snapshot.get("scenario_id")
    expected_raw = snapshot.get("scenario_expected_artifacts")
    expected = (
        [str(item) for item in expected_raw]
        if isinstance(expected_raw, list) else []
    )
    persisted = snapshot.get("scenario_artifact_status")

    if isinstance(persisted, list) and persisted:
        status = persisted
        summary = snapshot.get("scenario_artifact_summary") or {
            "present": sum(
                1 for entry in status
                if isinstance(entry, dict) and entry.get("present")
            ),
            "missing": sum(
                1 for entry in status
                if isinstance(entry, dict) and not entry.get("present")
            ),
            "total": len(status),
        }
    else:
        recheck = check_scenario_artifacts(expected, task_dir)
        status = [entry.to_dict() for entry in recheck]
        summary = summarize_artifact_status(recheck)

    base_url = f"/artifacts/{task_id}/"
    enriched = []
    for entry in status:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "")
        enriched.append({
            **entry,
            "url": base_url + path if path else None,
        })

    return JSONResponse({
        "task_id": task_id,
        "scenario_id": scenario_id,
        "scenario_title": snapshot.get("scenario_title"),
        "scenario_category": snapshot.get("scenario_category"),
        "expected_artifacts": expected,
        "status": enriched,
        "summary": summary,
    })


@router.get("/v1/tasks/{task_id}/scenario-quality-checks")
def get_scenario_quality_checks(task_id: str) -> JSONResponse:
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    task_dir = ARTIFACTS_ROOT / task_id
    try:
        snapshot = _load_pipeline_snapshot(task_dir)
    except FileNotFoundError:
        return JSONResponse({"detail": "task pipeline.json not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)

    specs_raw = snapshot.get("scenario_quality_checks") or []
    results_raw = snapshot.get("scenario_quality_check_results") or []
    summary = snapshot.get("scenario_quality_check_summary") or {
        "total": len(results_raw),
        "passed": sum(
            1 for result in results_raw
            if isinstance(result, dict) and result.get("passed")
        ),
        "failed": sum(
            1 for result in results_raw
            if isinstance(result, dict) and not result.get("passed")
        ),
        "blocking_failed": [
            result.get("id")
            for result in results_raw
            if isinstance(result, dict)
            and not result.get("passed")
            and result.get("blocking")
        ],
    }
    return JSONResponse({
        "task_id": task_id,
        "scenario_id": snapshot.get("scenario_id"),
        "scenario_title": snapshot.get("scenario_title"),
        "scenario_category": snapshot.get("scenario_category"),
        "specs": list(specs_raw) if isinstance(specs_raw, list) else [],
        "results": list(results_raw) if isinstance(results_raw, list) else [],
        "summary": summary,
    })


@router.get("/v1/runtime/capabilities")
def get_runtime_capabilities() -> JSONResponse:
    from backend.App.integrations.application.runtime_capabilities import summarize

    return JSONResponse(summarize())


@router.post("/v1/tasks/{task_id}/reveal")
def reveal_task_artifact_folder(task_id: str) -> JSONResponse:
    from backend.App.integrations.application.artifact_reveal import reveal
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    target = ARTIFACTS_ROOT / task_id
    payload = reveal(target, ARTIFACTS_ROOT)
    status = 200 if payload.get("ok") else 422
    return JSONResponse(payload, status_code=status)


@router.get("/v1/observability/cross-project")
def get_cross_project_observability() -> JSONResponse:
    from backend.App.integrations.application.observability_aggregator import (
        summarize_path,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    payload = summarize_path(ARTIFACTS_ROOT)
    return JSONResponse(payload)


@router.get("/v1/tasks/{task_id}/scenario-score")
def get_scenario_score(task_id: str) -> JSONResponse:
    from backend.App.orchestration.application.scenarios.scoring import (
        score_scenario_run,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    task_dir = ARTIFACTS_ROOT / task_id
    try:
        snapshot = _load_pipeline_snapshot(task_dir)
    except FileNotFoundError:
        return JSONResponse({"detail": "task pipeline.json not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=500)

    score = score_scenario_run(snapshot)
    return JSONResponse({
        "task_id": task_id,
        "scenario_id": snapshot.get("scenario_id"),
        "scenario_title": snapshot.get("scenario_title"),
        "scenario_category": snapshot.get("scenario_category"),
        **score.to_dict(),
    })


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
