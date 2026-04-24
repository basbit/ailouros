from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.App.integrations.application.ui_gateway import (
    remote_models_payload,
    ui_bundle_index,
    ui_models_payload,
)
from backend.App.shared.infrastructure.rest.live_stream import handle_ws_ui
from backend.UI.REST.schemas import UiRemoteModelsRequest

router = APIRouter()


def remote_openai_compatible_models_response(
    *,
    provider: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        content=remote_models_payload(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
        )
    )


@router.websocket("/ws/ui")
async def ws_ui(websocket: WebSocket) -> None:
    from backend.App.shared.infrastructure.rest.task_instance import task_store as _task_store

    await handle_ws_ui(websocket, _task_store)


@router.get("/ui/models")
def ui_models_proxy(
    provider: str = Query(..., description="ollama | lmstudio"),
) -> JSONResponse:
    try:
        return JSONResponse(content=ui_models_payload(provider))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ui/remote-models")
def ui_remote_models_proxy(body: UiRemoteModelsRequest) -> JSONResponse:
    return remote_openai_compatible_models_response(
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
    )


@router.get("/ui", response_class=HTMLResponse)
def ui():
    bundle_index = ui_bundle_index()
    if bundle_index.is_file():
        return FileResponse(bundle_index)
    return HTMLResponse(
        content="Vue frontend not built. Run: make frontend-build",
        status_code=503,
    )
