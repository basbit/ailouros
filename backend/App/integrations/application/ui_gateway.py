from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_UI_BUNDLE = _BACKEND_ROOT / "UI" / "Web"
_UI_BUNDLE_ROOT = Path(os.getenv("SWARM_FRONTEND_DIST", str(_DEFAULT_UI_BUNDLE)))


def ui_assets_root() -> Path:
    return _UI_BUNDLE_ROOT / "assets"


def ui_bundle_index() -> Path:
    return _UI_BUNDLE_ROOT / "index.html"


def ui_models_payload(provider: str) -> dict[str, Any]:
    from fastapi.responses import JSONResponse

    provider_normalized = (provider or "").strip().lower()
    if provider_normalized in {"ollama"}:
        from backend.App.integrations.infrastructure.model_proxy import ollama_models_proxy_response

        resp: JSONResponse = ollama_models_proxy_response()
        return resp.body and __import__("json").loads(resp.body) or {}
    if provider_normalized in {"lmstudio", "lm_studio"}:
        from backend.App.integrations.infrastructure.model_proxy import lmstudio_models_proxy_response

        resp = lmstudio_models_proxy_response()
        return resp.body and __import__("json").loads(resp.body) or {}
    raise ValueError(f"Unsupported provider: {provider_normalized!r}. Expected 'ollama' or 'lmstudio'.")


def remote_models_payload(
    *,
    provider: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    from backend.App.integrations.infrastructure.model_proxy import (
        remote_openai_compatible_models_dict,
    )

    return remote_openai_compatible_models_dict(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
    )
