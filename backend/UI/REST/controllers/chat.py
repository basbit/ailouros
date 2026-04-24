from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.App.orchestration.application.routing.pipeline_graph import (
    validate_pipeline_stages,
    validate_pipeline_steps,
)
from backend.App.orchestration.application.snapshot_serializer import pipeline_snapshot_for_disk
from backend.App.orchestration.application.streaming.chat_stream import stream_chat_chunks
from backend.App.orchestration.application.streaming.resume_stream import stream_human_resume_chunks
from backend.App.orchestration.application.streaming.retry_stream import stream_retry_chunks
from backend.App.orchestration.application.use_cases.tasks import (
    resolve_chat_request,
    start_pipeline_run,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json
from backend.UI.REST.schemas import (
    ChatCompletionRequest,
    HumanResumeRequest,
    RetryRequest,
    validate_agent_config,
)
from backend.App.shared.infrastructure.rest.sse_bridge import sync_to_async_sse
from backend.App.shared.infrastructure.rest.sse_response import DirectSSEResponse
from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT, task_store
from backend.App.shared.infrastructure.rest.utils import (
    _chat_sync_prepare_workspace_and_task,
    _extract_user_prompt,
    _openai_nonstream_response,
)

router = APIRouter()

_CHAT_STREAM_CONFIG = load_app_config_json("integrations_rest_misc.json").get(
    "chat_stream", {}
)
_RESUME_REQUEST_MODEL: str = _CHAT_STREAM_CONFIG.get(
    "resume_request_model", "swarm-resume"
)
_RETRY_REQUEST_MODEL: str = _CHAT_STREAM_CONFIG.get(
    "retry_request_model", "swarm-retry"
)

_STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse_headers(task_id: str) -> dict[str, str]:
    return {"X-Task-Id": task_id, **_STREAM_HEADERS}


@router.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    user_prompt = _extract_user_prompt(body.messages).strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="No user prompt found in messages")

    try:
        validate_agent_config(body.agent_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    agent_config, eff_steps = resolve_chat_request(body)

    eff_stages: Optional[list[list[str]]] = body.pipeline_stages
    if eff_stages is not None:
        try:
            validate_pipeline_stages(eff_stages, agent_config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if eff_steps is not None:
        try:
            validate_pipeline_steps(eff_steps, agent_config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        effective_prompt, workspace_path, meta_ws, task = await asyncio.to_thread(
            _chat_sync_prepare_workspace_and_task,
            user_prompt,
            body.workspace_root,
            body.workspace_write,
            task_store,
            body.project_context_file,
            agent_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_root_str = str(workspace_path) if workspace_path else ""
    workspace_apply_writes = bool(workspace_path and body.workspace_write)

    if body.stream:
        return DirectSSEResponse(
            sync_to_async_sse(
                request,
                lambda cancel_event: stream_chat_chunks(
                    original_prompt=user_prompt,
                    effective_prompt=effective_prompt,
                    request_model=body.model,
                    task_id=task["task_id"],
                    task_store=task_store,
                    artifacts_root=ARTIFACTS_ROOT,
                    agent_config=agent_config,
                    pipeline_steps=eff_steps,
                    pipeline_stages=eff_stages,
                    workspace_root_str=workspace_root_str,
                    workspace_apply_writes=workspace_apply_writes,
                    workspace_meta=meta_ws,
                    workspace_path=workspace_path if body.workspace_write else None,
                    cancel_event=cancel_event,
                ),
                task_id=task["task_id"],
            ),
            media_type="text/event-stream",
            headers=_sse_headers(task["task_id"]),
        )

    run_result = await asyncio.to_thread(
        start_pipeline_run,
        user_prompt=user_prompt,
        effective_prompt=effective_prompt,
        agent_config=agent_config,
        steps=eff_steps,
        workspace_root_str=workspace_root_str,
        workspace_apply_writes=workspace_apply_writes,
        workspace_path=workspace_path if body.workspace_write else None,
        workspace_meta=meta_ws,
        task_id=task["task_id"],
        task_store=task_store,
        artifacts_root=ARTIFACTS_ROOT,
        pipeline_snapshot_for_disk=pipeline_snapshot_for_disk,
    )

    if run_result["status"] == "awaiting_human":
        return JSONResponse(
            content={
                "error": {
                    "message": run_result["error"],
                    "type": "human_approval_required",
                    "step": run_result["human_approval_step"],
                }
            },
            status_code=409,
            headers={"X-Task-Id": task["task_id"]},
        )

    if run_result["status"] == "failed":
        return JSONResponse(
            content={
                "error": {
                    "message": f"Swarm pipeline failed: {run_result['exc_type']}",
                    "detail": run_result["error"],
                }
            },
            status_code=502,
            headers={"X-Task-Id": task["task_id"]},
        )

    response_payload = _openai_nonstream_response(run_result["final_text"], body.model)
    return JSONResponse(content=response_payload, headers={"X-Task-Id": task["task_id"]})


@router.post("/v1/tasks/{task_id}/human-resume")
async def human_resume(task_id: str, body: HumanResumeRequest, request: Request) -> Any:
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc

    if body.stream:
        return DirectSSEResponse(
            sync_to_async_sse(
                request,
                lambda cancel_event: stream_human_resume_chunks(
                    task_id,
                    body.feedback,
                    _RESUME_REQUEST_MODEL,
                    artifacts_root=ARTIFACTS_ROOT,
                    task_store=task_store,
                    cancel_event=cancel_event,
                    override_agent_config=body.agent_config,
                ),
                task_id=task_id,
            ),
            media_type="text/event-stream",
            headers=_sse_headers(task_id),
        )

    def _drain() -> None:
        for _ in stream_human_resume_chunks(
            task_id,
            body.feedback,
            _RESUME_REQUEST_MODEL,
            artifacts_root=ARTIFACTS_ROOT,
            task_store=task_store,
            override_agent_config=body.agent_config,
        ):
            pass

    await asyncio.to_thread(_drain)
    payload = task_store.get_task(task_id)
    return JSONResponse(content=payload, headers={"X-Task-Id": task_id})


@router.post("/v1/tasks/{task_id}/retry")
async def retry_from_failed_step(
    task_id: str, body: RetryRequest, request: Request
) -> Any:
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc

    if body.stream:
        return DirectSSEResponse(
            sync_to_async_sse(
                request,
                lambda cancel_event: stream_retry_chunks(
                    task_id,
                    _RETRY_REQUEST_MODEL,
                    artifacts_root=ARTIFACTS_ROOT,
                    task_store=task_store,
                    override_agent_config=body.agent_config,
                    from_step_override=body.from_step,
                    cancel_event=cancel_event,
                    retry_with=body.retry_with,
                    pipeline_steps_override=body.pipeline_steps,
                ),
                task_id=task_id,
            ),
            media_type="text/event-stream",
            headers=_sse_headers(task_id),
        )

    def _drain() -> None:
        for _ in stream_retry_chunks(
            task_id,
            _RETRY_REQUEST_MODEL,
            artifacts_root=ARTIFACTS_ROOT,
            task_store=task_store,
            override_agent_config=body.agent_config,
            from_step_override=body.from_step,
            retry_with=body.retry_with,
            pipeline_steps_override=body.pipeline_steps,
        ):
            pass

    await asyncio.to_thread(_drain)
    payload = task_store.get_task(task_id)
    return JSONResponse(content=payload, headers={"X-Task-Id": task_id})
