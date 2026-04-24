from __future__ import annotations

import logging
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
    from backend.App.workspace.application.use_cases.task_workspace_files import (
        get_task_workspace_diff,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    store = _get_task_store(request)
    try:
        result = get_task_workspace_diff(
            task_id=task_id,
            task_store=store,
            artifacts_root=ARTIFACTS_ROOT,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result.to_dict())


@router.get("/v1/tasks/{task_id}/workspace-file")
async def get_workspace_file(
    task_id: str,
    request: Request,
    path: str = Query(..., description="Relative path inside workspace"),
) -> JSONResponse:
    from backend.App.workspace.application.use_cases.task_workspace_files import (
        read_task_workspace_file,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    store = _get_task_store(request)
    try:
        result = read_task_workspace_file(
            task_id=task_id,
            relative_path=path,
            task_store=store,
            artifacts_root=ARTIFACTS_ROOT,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"path": result.path, "content": result.content})


@router.patch("/v1/tasks/{task_id}/workspace-file")
async def patch_workspace_file(
    task_id: str,
    body: WorkspaceFileEditRequest,
    request: Request,
) -> JSONResponse:
    from backend.App.workspace.application.use_cases.task_workspace_files import (
        patch_task_workspace_file,
    )
    from backend.App.shared.infrastructure.rest.task_instance import ARTIFACTS_ROOT

    store = _get_task_store(request)
    try:
        result = patch_task_workspace_file(
            task_id=task_id,
            relative_path=body.path,
            content=body.content,
            task_store=store,
            artifacts_root=ARTIFACTS_ROOT,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    logger.info(
        "workspace_file_edit: task=%s path=%s bytes=%d",
        task_id,
        body.path,
        len(body.content),
    )
    return JSONResponse({"ok": result.ok, "path": result.path})
