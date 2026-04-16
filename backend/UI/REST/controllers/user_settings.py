"""Global (cross-project) user settings — GET/PUT /v1/user/settings.

Stores settings in ~/.swarm/global_settings.json so they survive restarts
and are available to backend code (e.g. web search router) without requiring
them to be repeated in every API request.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.UI.REST.schemas import UserSettingsRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _global_settings_path() -> Path:
    """Return settings file path (overridable via SWARM_GLOBAL_SETTINGS_FILE for tests)."""
    override = os.environ.get("SWARM_GLOBAL_SETTINGS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".swarm" / "global_settings.json"


def _load() -> dict[str, str]:
    path = _global_settings_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: str(v) for k, v in data.items() if isinstance(v, str)}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("user_settings: load failed: %s", exc)
    return {}


def _save(data: dict[str, str]) -> None:
    path = _global_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("user_settings: save failed: %s", exc)


@router.get("/v1/user/settings")
async def get_user_settings() -> JSONResponse:
    """Return current global settings (API keys, etc.)."""
    data = _load()
    return JSONResponse({
        "tavily_api_key": data.get("tavily_api_key", ""),
        "exa_api_key": data.get("exa_api_key", ""),
        "scrapingdog_api_key": data.get("scrapingdog_api_key", ""),
    })


@router.put("/v1/user/settings")
async def put_user_settings(body: UserSettingsRequest) -> JSONResponse:
    """Persist global settings to ~/.swarm/global_settings.json."""
    data = _load()
    data["tavily_api_key"] = body.tavily_api_key
    data["exa_api_key"] = body.exa_api_key
    data["scrapingdog_api_key"] = body.scrapingdog_api_key
    _save(data)
    return JSONResponse({"ok": True})
