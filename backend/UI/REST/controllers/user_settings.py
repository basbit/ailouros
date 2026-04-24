from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.App.integrations.application.user_settings_service import (
    masked_user_settings,
    update_user_settings,
)

router = APIRouter()


class UserSettingsPayload(BaseModel):
    # Secret API keys (masked on GET, stored in env)
    tavily_api_key: str = ""
    exa_api_key: str = ""
    scrapingdog_api_key: str = ""

    # Global Automation & Quality settings (persisted in var/user_settings.json)
    swarm_self_verify: bool = False
    swarm_self_verify_model: str = ""
    swarm_self_verify_provider: str = ""
    swarm_auto_approve: str = ""
    swarm_auto_approve_timeout: str = ""
    swarm_auto_retry: bool = False
    swarm_max_step_retries: str = ""
    swarm_deep_planning: bool = False
    swarm_deep_planning_model: str = ""
    swarm_deep_planning_provider: str = ""
    swarm_background_agent: bool = False
    swarm_background_agent_model: str = ""
    swarm_background_agent_provider: str = ""
    swarm_background_watch_paths: str = ""
    swarm_dream_enabled: bool = False
    swarm_quality_gate: bool = False
    swarm_auto_plan: bool = False
    swarm_planner_model: str = ""
    swarm_planner_provider: str = ""

    model_config = {"extra": "ignore"}


@router.get("/v1/user/settings")
async def get_user_settings() -> JSONResponse:
    return JSONResponse(masked_user_settings())


@router.put("/v1/user/settings")
async def put_user_settings(payload: UserSettingsPayload) -> JSONResponse:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    return JSONResponse(update_user_settings(data))
