from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.App.workspace.application.use_cases.project_settings import (
    get_project_settings_payload,
    save_project_settings_payload,
)
from backend.UI.REST.schemas import ProjectSettingsRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/v1/project/settings")
async def get_project_settings(
    workspace_root: str = Query(..., min_length=1),
) -> JSONResponse:
    try:
        payload = get_project_settings_payload(workspace_root)
    except (OSError, ValueError) as exc:
        logger.warning("project_settings.get failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(payload)


@router.put("/v1/project/settings")
async def put_project_settings(body: ProjectSettingsRequest) -> JSONResponse:
    try:
        payload = save_project_settings_payload(body.workspace_root, body.settings)
    except (OSError, ValueError) as exc:
        logger.warning("project_settings.put failed: %s", exc)
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(payload)
