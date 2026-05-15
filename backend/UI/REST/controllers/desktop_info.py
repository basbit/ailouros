from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.App.workspace.application.use_cases.desktop_project_init import (
    desktop_info_payload,
    init_desktop_project_workspace,
)
from backend.UI.REST.schemas import DesktopProjectInitRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/desktop/info")
async def get_desktop_info() -> JSONResponse:
    return JSONResponse(desktop_info_payload())


@router.post("/v1/desktop/projects/init")
async def init_desktop_project(body: DesktopProjectInitRequest) -> JSONResponse:
    try:
        workspace_root = init_desktop_project_workspace(body.project_id)
    except ValueError as exc:
        logger.warning("desktop_projects.init failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.warning("desktop_projects.init filesystem error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"workspace_root": str(workspace_root)})
