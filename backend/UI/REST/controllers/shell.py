"""Approval endpoints: shell commands & human review gates.

Routes:
    GET  /v1/tasks/{task_id}/pending-shell   — list pending shell commands
    POST /v1/tasks/{task_id}/confirm-shell   — approve or reject shell commands
    GET  /v1/tasks/{task_id}/pending-human   — check pending human approval
    POST /v1/tasks/{task_id}/confirm-human   — approve or reject human gate
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.App.orchestration.infrastructure.human_approval import (
    complete_human_approval,
    pending_human_context,
)
from backend.App.orchestration.infrastructure.shell_approval import (
    complete_shell_approval,
    pending_shell_commands,
)
from backend.UI.REST.schemas import _HumanConfirmRequest, _ShellConfirmRequest
from backend.UI.REST.task_instance import task_store

router = APIRouter()


@router.get("/v1/tasks/{task_id}/pending-shell")
async def get_pending_shell(task_id: str) -> JSONResponse:
    """Return the list of shell commands waiting for user approval."""
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    cmds = pending_shell_commands(task_id)
    return JSONResponse(
        content={"task_id": task_id, "commands": cmds or [], "pending": cmds is not None}
    )


@router.post("/v1/tasks/{task_id}/confirm-shell")
async def confirm_shell(task_id: str, req: _ShellConfirmRequest) -> JSONResponse:
    """Approve or reject pending shell commands for a task.

    Waits briefly for the pipeline thread to register pending commands
    (race condition: frontend may POST before pipeline thread writes to _PENDING_SHELL).
    """
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    for _ in range(50):
        if pending_shell_commands(task_id) is not None:
            break
        await asyncio.sleep(0.1)
    if pending_shell_commands(task_id) is None:
        raise HTTPException(status_code=409, detail="No pending shell approval for this task")
    complete_shell_approval(task_id, req.approved)
    return JSONResponse(content={"ok": True, "approved": req.approved})


# ---------------------------------------------------------------------------
# Human review gate
# ---------------------------------------------------------------------------

@router.get("/v1/tasks/{task_id}/pending-human")
async def get_pending_human(task_id: str) -> JSONResponse:
    """Return the context waiting for human approval."""
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    ctx = pending_human_context(task_id)
    response: dict[str, Any] = {
        "task_id": task_id, "context": ctx or "", "pending": ctx is not None,
    }
    # Parse structured clarify questions if present
    if ctx and "NEEDS_CLARIFICATION" in ctx:
        from backend.App.orchestration.application.nodes.clarify_parser import parse_clarify_questions
        questions = parse_clarify_questions(ctx)
        if questions:
            response["questions"] = [
                {"index": q.index, "text": q.text, "options": q.options}
                for q in questions
            ]
    return JSONResponse(content=response)


@router.post("/v1/tasks/{task_id}/confirm-human")
async def confirm_human(task_id: str, req: _HumanConfirmRequest) -> JSONResponse:
    """Approve or reject a human review gate."""
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    for _ in range(50):
        if pending_human_context(task_id) is not None:
            break
        await asyncio.sleep(0.1)
    if pending_human_context(task_id) is None:
        raise HTTPException(status_code=409, detail="No pending human approval for this task")
    complete_human_approval(task_id, req.approved, req.user_input)
    return JSONResponse(content={"ok": True, "approved": req.approved})
