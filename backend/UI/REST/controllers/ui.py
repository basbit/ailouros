"""UI routes: /ui, /ws/ui, /ui/remote-models, /ui/models."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.UI.REST.presentation.live import handle_ws_ui, metrics_payload
from backend.UI.REST.schemas import UiRemoteModelsRequest
from backend.App.integrations.infrastructure.model_proxy import (
    lmstudio_models_proxy_response,
    ollama_models_proxy_response,
    remote_openai_compatible_models_response,
)

_UI_BUNDLE_INDEX = Path(__file__).resolve().parents[2] / "Web" / "index.html"

router = APIRouter()


@router.get("/metrics/live")
def metrics_live():
    """Live pipeline metrics payload (JSON) — distinct from GET /metrics (Prometheus text)."""
    return metrics_payload()


@router.websocket("/ws/ui")
async def ws_ui(websocket: WebSocket) -> None:
    from backend.UI.REST.task_instance import task_store as _task_store
    await handle_ws_ui(websocket, _task_store)


@router.get("/ui/models")
def ui_models_proxy(
    provider: str = Query(..., description="ollama | lmstudio"),
) -> JSONResponse:
    p = (provider or "").strip().lower()
    if p == "ollama":
        return ollama_models_proxy_response()
    if p == "lmstudio":
        return lmstudio_models_proxy_response()
    raise HTTPException(status_code=400, detail="provider must be ollama or lmstudio")


@router.post("/ui/remote-models")
def ui_remote_models_proxy(body: UiRemoteModelsRequest) -> JSONResponse:
    """List models from a remote OpenAI-compatible API."""
    return remote_openai_compatible_models_response(
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
    )


@router.get("/ui", response_class=HTMLResponse)
def ui():
    if _UI_BUNDLE_INDEX.is_file():
        return FileResponse(_UI_BUNDLE_INDEX)
    return HTMLResponse(
        content="Vue frontend not built. Run: make frontend-build",
        status_code=503,
    )
