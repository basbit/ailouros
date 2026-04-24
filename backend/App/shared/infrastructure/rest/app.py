from __future__ import annotations

import logging
import uuid as _uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from backend.App.integrations.application.rest_misc_service import (
    start_update_check_background,
)
from backend.App.integrations.application.ui_gateway import (
    ui_assets_root,
)
from backend.App.shared.application.request_context import (
    set_request_id,
)
from backend.App.shared.infrastructure.rest.container import AppContainer
from backend.UI.REST.controllers.chat import router as _router_tasks
from backend.UI.REST.controllers.memory import router as _router_memory
from backend.UI.REST.controllers.misc import router as _router_misc
from backend.UI.REST.controllers.onboarding import router as _router_onboarding
from backend.UI.REST.controllers.pipelines import router as _router_pipelines
from backend.UI.REST.controllers.project_settings import (
    router as _router_project_settings,
)
from backend.UI.REST.controllers.shell import router as _router_shell
from backend.UI.REST.controllers.tasks import router as _router_task_endpoints
from backend.UI.REST.controllers.ui import router as _router_ui
from backend.App.integrations.application.user_settings_service import (
    load_and_apply_user_settings as _load_and_apply_user_settings,
)
from backend.UI.REST.controllers.user_settings import (
    router as _router_user_settings,
)
from backend.UI.REST.controllers.wiki import router as _router_wiki
from backend.UI.REST.controllers.workspace import router as _router_workspace
from backend.App.shared.infrastructure.rest.task_instance import (
    ARTIFACTS_ROOT,
    task_store as _task_store_instance,
)

logger = logging.getLogger(__name__)


task_store = _task_store_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = AppContainer()
    container.wire(app)

    _load_and_apply_user_settings()

    start_update_check_background()
    try:
        yield
    finally:
        await container.teardown(app)


app = FastAPI(title="AIlourOS Orchestrator", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next: Any) -> Any:

    request_id = request.headers.get("X-Request-ID") or str(_uuid.uuid4())[:8]
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_ROOT)), name="artifacts")

_UI_ASSETS_ROOT = ui_assets_root()
if _UI_ASSETS_ROOT.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_UI_ASSETS_ROOT)),
        name="ui_assets",
    )


app.include_router(_router_misc)
app.include_router(_router_memory)
app.include_router(_router_onboarding)
app.include_router(_router_tasks)
app.include_router(_router_task_endpoints)
app.include_router(_router_ui)
app.include_router(_router_shell)
app.include_router(_router_workspace)
app.include_router(_router_wiki)
app.include_router(_router_pipelines)
app.include_router(_router_project_settings)
app.include_router(_router_user_settings)
