from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.App.orchestration.application.approvals_facade import (
    human_complete_approval,
    human_pending_context,
    human_pending_payload,
    manual_shell_complete,
    manual_shell_pending_payload,
    shell_complete_approval,
    shell_pending_commands,
    shell_pending_payload,
)
from backend.UI.REST.schemas import (
    _HumanConfirmRequest,
    _ManualShellConfirmRequest,
    _ShellConfirmRequest,
)
from backend.App.shared.infrastructure.rest.task_instance import task_store

router = APIRouter()


def _require_task(task_id: str) -> None:
    try:
        task_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


async def _wait_for_pending(
    predicate,
    *,
    attempts: int = 50,
    delay: float = 0.1,
) -> bool:
    for _ in range(attempts):
        if predicate() is not None:
            return True
        await asyncio.sleep(delay)
    return predicate() is not None


@router.get("/v1/tasks/{task_id}/pending-shell")
async def get_pending_shell(task_id: str) -> JSONResponse:
    _require_task(task_id)
    payload = shell_pending_payload(task_id)
    if payload is None:
        return JSONResponse(
            content={
                "task_id": task_id,
                "commands": [],
                "needs_allowlist": [],
                "already_allowed": [],
                "pending": False,
            }
        )
    return JSONResponse(
        content={
            "task_id": task_id,
            "commands": payload["commands"],
            "needs_allowlist": payload["needs_allowlist"],
            "already_allowed": payload["already_allowed"],
            "pending": True,
        }
    )


@router.post("/v1/tasks/{task_id}/confirm-shell")
async def confirm_shell(task_id: str, body: _ShellConfirmRequest) -> JSONResponse:
    _require_task(task_id)
    if not await _wait_for_pending(lambda: shell_pending_commands(task_id)):
        raise HTTPException(
            status_code=409, detail="No pending shell approval for this task"
        )
    shell_complete_approval(task_id, body.approved)
    return JSONResponse(content={"ok": True, "approved": body.approved})


@router.get("/v1/tasks/{task_id}/pending-manual-shell")
async def get_pending_manual_shell(task_id: str) -> JSONResponse:
    _require_task(task_id)
    payload = manual_shell_pending_payload(task_id)
    if payload is None:
        return JSONResponse(
            content={
                "task_id": task_id,
                "commands": [],
                "reason": "",
                "pending": False,
            }
        )
    return JSONResponse(
        content={
            "task_id": task_id,
            "commands": payload["commands"],
            "reason": payload["reason"],
            "pending": True,
        }
    )


@router.post("/v1/tasks/{task_id}/confirm-manual-shell")
async def confirm_manual_shell(
    task_id: str,
    body: _ManualShellConfirmRequest,
) -> JSONResponse:
    _require_task(task_id)
    if not await _wait_for_pending(lambda: manual_shell_pending_payload(task_id)):
        raise HTTPException(
            status_code=409,
            detail="No pending manual-shell approval for this task",
        )
    manual_shell_complete(task_id, body.done)
    return JSONResponse(content={"ok": True, "done": body.done})


@router.get("/v1/tasks/{task_id}/pending-human")
async def get_pending_human(task_id: str) -> JSONResponse:
    _require_task(task_id)
    return JSONResponse(content=human_pending_payload(task_id))


@router.post("/v1/tasks/{task_id}/confirm-human")
async def confirm_human(task_id: str, body: _HumanConfirmRequest) -> JSONResponse:
    _require_task(task_id)
    if not await _wait_for_pending(lambda: human_pending_context(task_id)):
        raise HTTPException(
            status_code=409, detail="No pending human approval for this task"
        )
    human_complete_approval(task_id, body.approved, body.user_input)
    return JSONResponse(content={"ok": True, "approved": body.approved})
