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
    if provider_normalized in {"local", "llamacpp"}:
        return _local_models_payload()
    raise ValueError(
        f"Unsupported provider: {provider_normalized!r}. Expected 'ollama' | 'lmstudio' | 'local'."
    )


_LOCAL_LLAMA_ALIAS = "local-default"


def _local_models_payload() -> dict[str, Any]:
    raw_dir = os.getenv("AILOUROS_MODELS_DIR", "").strip()
    if not raw_dir:
        return {"ok": True, "models": []}
    models_dir = Path(raw_dir).expanduser()
    if not models_dir.is_dir():
        return {"ok": True, "models": []}

    available_stems = sorted(p.stem for p in models_dir.glob("*.gguf"))
    if not available_stems:
        return {"ok": True, "models": []}

    catalog, default_stem = _load_local_model_catalog()
    active_stem = default_stem if default_stem in available_stems else available_stems[0]
    label = catalog.get(active_stem, active_stem)
    alias = os.getenv("SWARM_MODEL", _LOCAL_LLAMA_ALIAS).strip() or _LOCAL_LLAMA_ALIAS
    return {
        "ok": True,
        "models": [{"id": alias, "label": label, "source_file": active_stem}],
    }


def _load_local_model_catalog() -> tuple[dict[str, str], str]:
    raw_path = os.getenv("AILOUROS_DEFAULT_MODELS_MANIFEST", "").strip()
    if not raw_path:
        return {}, ""
    manifest_path = Path(raw_path).expanduser()
    if not manifest_path.is_file():
        return {}, ""
    try:
        import json

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("ui_gateway: cannot read local models manifest %s: %s", manifest_path, exc)
        return {}, ""
    catalog: dict[str, str] = {}
    for entry in data.get("models", []):
        model_id = str(entry.get("id", "")).strip()
        label = str(entry.get("label", "")).strip()
        if model_id and label:
            catalog[model_id] = label
    default_id = str(data.get("default_model_id", "")).strip()
    return catalog, default_id


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
