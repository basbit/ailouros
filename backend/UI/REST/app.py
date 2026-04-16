"""Composition root for the AIlourOS backend.

The full FastAPI application (with lifespan, middleware, static files, and all routes)
lives here.  ``orchestrator/app.py`` re-exports the ``app`` object from this module
for backward compatibility with ``orchestrator_api.py``.

Startup entrypoint:
    uvicorn backend.UI.REST.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import uuid as _uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.App.integrations.infrastructure.observability.logging_config import set_request_id
from backend.UI.REST.container import AppContainer
from backend.UI.REST.task_instance import ARTIFACTS_ROOT, task_store as _task_store_instance
from backend.UI.REST.controllers.misc import router as _router_misc
from backend.UI.REST.controllers.memory import router as _router_memory
from backend.UI.REST.controllers.onboarding import router as _router_onboarding
from backend.UI.REST.controllers.chat import router as _router_tasks
from backend.UI.REST.controllers.tasks import router as _router_task_endpoints
from backend.UI.REST.controllers.schedules import router as _router_schedules
from backend.UI.REST.controllers.ui import router as _router_ui
from backend.UI.REST.controllers.shell import router as _router_shell
from backend.UI.REST.controllers.workspace import router as _router_workspace
from backend.UI.REST.controllers.wiki import router as _router_wiki
from backend.UI.REST.controllers.pipelines import router as _router_pipelines
from backend.UI.REST.controllers.user_settings import router as _router_user_settings

logger = logging.getLogger(__name__)

# Re-export so external modules can do: from backend.UI.REST.app import task_store
task_store = _task_store_instance


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    container = AppContainer()
    container.wire(app)
    # Kick off git update check in the background — never blocks startup.
    # Surfaces via GET /v1/system/update-available so the UI can show a banner.
    try:
        from backend.App.integrations.infrastructure.update_check import (
            run_update_check_in_background,
        )
        run_update_check_in_background()
    except Exception:  # pragma: no cover — defensive; never block lifespan
        pass
    try:
        yield
    finally:
        await container.teardown(app)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(title="AIlourOS Orchestrator", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next: Any) -> Any:
    """Propagate X-Request-ID through structured logs and response headers."""
    rid = request.headers.get("X-Request-ID") or str(_uuid.uuid4())[:8]
    set_request_id(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# Static files
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_ROOT)), name="artifacts")

_UI_ASSETS_ROOT = Path(__file__).resolve().parents[1] / "Web" / "assets"
if _UI_ASSETS_ROOT.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_UI_ASSETS_ROOT)),
        name="ui_assets",
    )

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(_router_misc)
app.include_router(_router_memory)
app.include_router(_router_onboarding)
app.include_router(_router_tasks)
app.include_router(_router_task_endpoints)
app.include_router(_router_schedules)
app.include_router(_router_ui)
app.include_router(_router_shell)
app.include_router(_router_workspace)
app.include_router(_router_wiki)
app.include_router(_router_pipelines)
app.include_router(_router_user_settings)
