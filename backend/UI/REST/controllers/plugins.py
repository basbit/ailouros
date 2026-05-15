from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.App.plugins.application.use_cases import (
    PluginUseCaseError,
    install_plugin,
    installed,
    list_registries,
    refresh_registry,
    register_registry,
    search,
    uninstall_plugin,
    verify,
)
from backend.App.plugins.infrastructure.plugin_store_fs import (
    PluginIntegrityError,
    PluginNotFoundError,
)
from backend.App.plugins.infrastructure.registry_client import (
    BlobIntegrityError,
    RegistryFetchError,
)

router = APIRouter()


class RegisterRegistryBody(BaseModel):
    url: str
    name: str


class InstallPluginBody(BaseModel):
    id: str
    version: str
    registry: str
    allow_unsigned: bool = False


def _manifest_to_dict(manifest: Any) -> dict[str, Any]:
    return {
        "id": manifest.id,
        "version": manifest.version,
        "kind": manifest.kind,
        "compat": manifest.compat,
        "title": manifest.title,
        "description": manifest.description,
        "author": manifest.author,
        "license": manifest.license,
        "signed": manifest.signature is not None,
        "depends_on": list(manifest.depends_on),
        "entries": [{"path": e.path, "target": e.target} for e in manifest.entries],
    }


@router.get("/v1/plugins")
def list_installed() -> dict[str, Any]:
    return {"plugins": [_manifest_to_dict(m) for m in installed()]}


@router.get("/v1/plugins/registries")
def get_registries() -> dict[str, Any]:
    return {"registries": list_registries()}


@router.post("/v1/plugins/registries")
def add_registry(body: RegisterRegistryBody) -> dict[str, Any]:
    try:
        register_registry(body.url, body.name)
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"registered": True, "name": body.name, "url": body.url}


@router.post("/v1/plugins/registries/{name}/refresh")
def refresh_registry_route(name: str) -> dict[str, Any]:
    try:
        listing = refresh_registry(name)
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RegistryFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "registry_id": listing.registry_id,
        "registry_url": listing.registry_url,
        "updated_at": listing.updated_at,
        "plugin_count": len(listing.plugins),
    }


@router.get("/v1/plugins/search")
def search_plugins(q: str = Query(default="")) -> dict[str, Any]:
    try:
        results = search(q)
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RegistryFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"results": results}


@router.post("/v1/plugins/install")
def install_plugin_route(body: InstallPluginBody) -> dict[str, Any]:
    try:
        manifest = install_plugin(
            body.id,
            body.version,
            body.registry,
            allow_unsigned=body.allow_unsigned,
        )
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (PluginIntegrityError, BlobIntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RegistryFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"installed": True, "plugin": _manifest_to_dict(manifest)}


@router.delete("/v1/plugins/{plugin_id:path}")
def uninstall_plugin_route(plugin_id: str, version: Optional[str] = Query(default=None)) -> dict[str, Any]:
    try:
        uninstall_plugin(plugin_id, version)
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PluginNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"uninstalled": True, "id": plugin_id}


@router.get("/v1/plugins/{plugin_id:path}/verify")
def verify_plugin(plugin_id: str, version: str = Query(...)) -> dict[str, Any]:
    try:
        return verify(plugin_id, version)
    except PluginUseCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
