"""Workspace file inspection and editing endpoints.

GET   /v1/tasks/{task_id}/workspace-diff        — return captured diff for human review gate
GET   /v1/tasks/{task_id}/workspace-file?path=  — read a workspace file (for inline edit)
PATCH /v1/tasks/{task_id}/workspace-file        — inline-edit a workspace file
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_task_store(request: Request) -> Any:
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise RuntimeError(
            "task_store not initialized on app.state. "
            "Ensure lifespan startup completed successfully."
        )
    return store


class WorkspaceFileEditRequest(BaseModel):
    path: str
    content: str


@router.get("/v1/tasks/{task_id}/workspace-diff")
async def get_workspace_diff(task_id: str, request: Request) -> JSONResponse:
    """Return the captured workspace diff for *task_id* so the human review gate can show it."""
    store = _get_task_store(request)
    try:
        store.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    # Read diff from pipeline.json (written by stream_finalise / pipeline_enforcement)
    from backend.UI.REST.task_instance import ARTIFACTS_ROOT
    diff_data: dict[str, Any] = {
        "diff_text": "",
        "files_changed": [],
        "stats": {"added": 0, "removed": 0, "files": 0},
        "source": "none",
    }
    pipeline_json = ARTIFACTS_ROOT / task_id / "pipeline.json"
    if pipeline_json.is_file():
        try:
            raw = json.loads(pipeline_json.read_text(encoding="utf-8"))
            stored = raw.get("dev_workspace_diff")
            if isinstance(stored, dict):
                diff_data = stored
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "get_workspace_diff: could not read pipeline.json for task %s: %s",
                task_id,
                exc,
            )

    return JSONResponse(diff_data)


@router.get("/v1/tasks/{task_id}/workspace-file")
async def get_workspace_file(
    task_id: str,
    request: Request,
    path: str = Query(..., description="Relative path inside workspace"),
) -> JSONResponse:
    """Return the content of a file inside the task's workspace for inline editing."""
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path

    store = _get_task_store(request)
    try:
        store.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    from backend.UI.REST.task_instance import ARTIFACTS_ROOT
    pipeline_json = ARTIFACTS_ROOT / task_id / "pipeline.json"
    workspace_root_raw = ""
    if pipeline_json.is_file():
        try:
            raw = json.loads(pipeline_json.read_text(encoding="utf-8"))
            workspace_root_raw = str(raw.get("workspace_root") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass

    if not workspace_root_raw:
        raise HTTPException(status_code=400, detail="Task has no workspace_root")

    workspace_root = Path(workspace_root_raw).resolve()
    try:
        target = safe_relative_path(workspace_root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        content = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({"path": path, "content": content})


@router.patch("/v1/tasks/{task_id}/workspace-file")
async def patch_workspace_file(
    task_id: str,
    req: WorkspaceFileEditRequest,
    request: Request,
) -> JSONResponse:
    """Write *content* to *path* inside the task's workspace.

    Requires ``SWARM_ALLOW_WORKSPACE_WRITE=1``.
    """
    from backend.App.workspace.infrastructure.patch_parser import safe_relative_path

    if os.getenv("SWARM_ALLOW_WORKSPACE_WRITE", "0").strip() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=403,
            detail=(
                "SWARM_ALLOW_WORKSPACE_WRITE is not enabled. "
                "Set it to 1 to allow inline file edits."
            ),
        )

    store = _get_task_store(request)
    try:
        store.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    # Read workspace_root from pipeline.json
    from backend.UI.REST.task_instance import ARTIFACTS_ROOT
    pipeline_json = ARTIFACTS_ROOT / task_id / "pipeline.json"
    workspace_root_raw = ""
    if pipeline_json.is_file():
        try:
            raw = json.loads(pipeline_json.read_text(encoding="utf-8"))
            workspace_root_raw = str(raw.get("workspace_root") or "").strip()
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "patch_workspace_file: could not read pipeline.json for task %s: %s",
                task_id,
                exc,
            )

    if not workspace_root_raw:
        raise HTTPException(status_code=400, detail="Task has no workspace_root")

    workspace_root = Path(workspace_root_raw).resolve()
    try:
        target = safe_relative_path(workspace_root, req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    logger.info(
        "workspace_file_edit: task=%s path=%s bytes=%d",
        task_id,
        req.path,
        len(req.content),
    )

    return JSONResponse({"ok": True, "path": req.path})
