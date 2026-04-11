"""Task routes: /v1/chat/completions, /v1/tasks/*/human-resume,
/v1/tasks/*/retry, /v1/tasks/*/pending-shell, /v1/tasks/*/confirm-shell.

Task CRUD (/tasks/{task_id}, cancel) → controllers/tasks.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.UI.REST.schemas import (
    ChatCompletionRequest,
    HumanResumeRequest,
    RetryRequest,
    validate_agent_config,
)
from backend.UI.REST.task_instance import task_store, ARTIFACTS_ROOT
from backend.App.orchestration.application.pipeline_graph import validate_pipeline_steps

router = APIRouter()


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    from backend.UI.REST.utils import (
        _extract_user_prompt,
        _chat_sync_prepare_workspace_and_task,
        _openai_nonstream_response,
        _pipeline_snapshot_for_disk,
        _workspace_followup_lines,
    )
    from backend.App.orchestration.application.tasks import (
        resolve_chat_request as _resolve_chat_request,
        start_pipeline_run,
    )
    from backend.UI.REST.presentation.sse import _DirectSSEResponse
    from backend.UI.REST.presentation.stream_handlers import _sync_sse_generator_to_async, _stream_chat_chunks

    user_prompt = _extract_user_prompt(req.messages).strip()
    if not user_prompt:
        raise HTTPException(status_code=400, detail="No user prompt found in messages")

    try:
        validate_agent_config(req.agent_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    agent_config, eff_steps = _resolve_chat_request(req)

    # Topology is in agent_config.swarm.topology — pipeline_runners reads it
    # to choose linear vs graph-based execution. User's pipeline_steps always respected.
    if eff_steps is not None:
        try:
            validate_pipeline_steps(eff_steps, agent_config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_root = req.workspace_root
    workspace_write = req.workspace_write
    project_context_file = req.project_context_file
    try:
        effective_prompt, workspace_path, meta_ws, task = await asyncio.to_thread(
            _chat_sync_prepare_workspace_and_task,
            user_prompt,
            workspace_root,
            workspace_write,
            task_store,
            project_context_file,
            agent_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    workspace_root_str = str(workspace_path) if workspace_path else ""
    workspace_apply_writes = bool(workspace_path and workspace_write)

    if req.stream:
        stream_headers = {
            "X-Task-Id": task["task_id"],
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return _DirectSSEResponse(
            _sync_sse_generator_to_async(
                request,
                lambda cancel_event: _stream_chat_chunks(
                    original_prompt=user_prompt,
                    effective_prompt=effective_prompt,
                    request_model=req.model,
                    task_id=task["task_id"],
                    task_store=task_store,
                    artifacts_root=ARTIFACTS_ROOT,
                    agent_config=agent_config,
                    pipeline_steps=eff_steps,
                    workspace_root_str=workspace_root_str,
                    workspace_apply_writes=workspace_apply_writes,
                    workspace_meta=meta_ws,
                    workspace_path=workspace_path if req.workspace_write else None,
                    cancel_event=cancel_event,
                ),
                task_id=task["task_id"],
            ),
            media_type="text/event-stream",
            headers=stream_headers,
        )

    from backend.App.integrations.infrastructure.observability.logging_config import set_task_id
    set_task_id(task["task_id"])

    run_result = await asyncio.to_thread(
        start_pipeline_run,
        user_prompt=user_prompt,
        effective_prompt=effective_prompt,
        agent_config=agent_config,
        steps=eff_steps,
        workspace_root_str=workspace_root_str,
        workspace_apply_writes=workspace_apply_writes,
        workspace_path=workspace_path if req.workspace_write else None,
        workspace_meta=meta_ws,
        task_id=task["task_id"],
        task_store=task_store,
        artifacts_root=ARTIFACTS_ROOT,
        pipeline_snapshot_for_disk=_pipeline_snapshot_for_disk,
        workspace_followup_lines=_workspace_followup_lines,
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

    response = _openai_nonstream_response(run_result["final_text"], req.model)
    return JSONResponse(content=response, headers={"X-Task-Id": task["task_id"]})


@router.post("/v1/tasks/{task_id}/human-resume")
async def human_resume(task_id: str, req: HumanResumeRequest, request: Request) -> Any:
    from backend.UI.REST.presentation.sse import _DirectSSEResponse
    from backend.UI.REST.presentation.stream_handlers import (
        _sync_sse_generator_to_async,
        _stream_human_resume_chunks,
    )

    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc

    if req.stream:
        return _DirectSSEResponse(
            _sync_sse_generator_to_async(
                request,
                lambda cancel_event: _stream_human_resume_chunks(
                    task_id, req.feedback, "swarm-resume",
                    artifacts_root=ARTIFACTS_ROOT,
                    task_store=task_store,
                    cancel_event=cancel_event,
                    override_agent_config=req.agent_config,
                ),
                task_id=task_id,
            ),
            media_type="text/event-stream",
            headers={
                "X-Task-Id": task_id,
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _drain() -> None:
        for _ in _stream_human_resume_chunks(
            task_id, req.feedback, "swarm-resume",
            artifacts_root=ARTIFACTS_ROOT,
            task_store=task_store,
            override_agent_config=req.agent_config,
        ):
            pass

    await asyncio.to_thread(_drain)
    payload = task_store.get_task(task_id)
    return JSONResponse(content=payload, headers={"X-Task-Id": task_id})


@router.post("/v1/tasks/{task_id}/retry")
async def retry_from_failed_step(task_id: str, req: RetryRequest, request: Request) -> Any:
    """Retry a failed pipeline from the step that failed (or from req.from_step)."""
    from backend.UI.REST.presentation.sse import _DirectSSEResponse
    from backend.UI.REST.presentation.stream_handlers import (
        _sync_sse_generator_to_async,
        _stream_retry_chunks,
    )

    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc

    if req.stream:
        return _DirectSSEResponse(
            _sync_sse_generator_to_async(
                request,
                lambda cancel_event: _stream_retry_chunks(
                    task_id, "swarm-retry",
                    artifacts_root=ARTIFACTS_ROOT,
                    task_store=task_store,
                    override_agent_config=req.agent_config,
                    from_step_override=req.from_step,
                    cancel_event=cancel_event,
                    retry_with=req.retry_with,
                    pipeline_steps_override=req.pipeline_steps,
                ),
                task_id=task_id,
            ),
            media_type="text/event-stream",
            headers={
                "X-Task-Id": task_id,
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _drain() -> None:
        for _ in _stream_retry_chunks(
            task_id, "swarm-retry",
            artifacts_root=ARTIFACTS_ROOT,
            task_store=task_store,
            override_agent_config=req.agent_config,
            from_step_override=req.from_step,
            retry_with=req.retry_with,
            pipeline_steps_override=req.pipeline_steps,
        ):
            pass

    await asyncio.to_thread(_drain)
    payload = task_store.get_task(task_id)
    return JSONResponse(content=payload, headers={"X-Task-Id": task_id})
