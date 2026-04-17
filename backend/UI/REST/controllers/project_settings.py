"""Project-scoped UI settings endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.App.workspace.infrastructure.project_settings import (
    load_project_settings,
    project_settings_path,
    save_project_settings,
)
from backend.UI.REST.schemas import ProjectSettingsRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/project/settings")
async def get_project_settings(workspace_root: str = Query(..., min_length=1)) -> JSONResponse:
    try:
        settings = load_project_settings(workspace_root)
        path = project_settings_path(workspace_root)
    except (OSError, ValueError) as exc:
        logger.warning("project_settings.get failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "exists": settings is not None,
            "path": str(path),
            "settings": settings,
        }
    )


@router.put("/v1/project/settings")
async def put_project_settings(body: ProjectSettingsRequest) -> JSONResponse:
    try:
        path = save_project_settings(body.workspace_root, body.settings)
    except (OSError, ValueError) as exc:
        logger.warning("project_settings.put failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "path": str(path)})
