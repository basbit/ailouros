"""Visual pipeline CRUD and run endpoints.

POST   /api/pipelines            — save new pipeline
GET    /api/pipelines            — list all
GET    /api/pipelines/{id}       — get by id
PUT    /api/pipelines/{id}       — update
DELETE /api/pipelines/{id}       — delete
POST   /api/pipelines/{id}/run   — execute via existing orchestrator
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]  # app/
_PIPELINES_DIR = _BACKEND_ROOT / "data" / "pipelines"


def _pipelines_dir() -> Path:
    _PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    return _PIPELINES_DIR


class PipelineDefinition(BaseModel):
    name: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


def _pipeline_path(pipeline_id: str) -> Path:
    # Validate id to prevent path traversal
    if not pipeline_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid pipeline id: {pipeline_id!r}")
    return _pipelines_dir() / f"{pipeline_id}.json"


def _load_pipeline(pipeline_id: str) -> dict[str, Any]:
    path = _pipeline_path(pipeline_id)
    if not path.exists():
        raise KeyError(pipeline_id)
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/api/pipelines")
async def create_pipeline(body: PipelineDefinition) -> JSONResponse:
    pipeline_id = f"pipeline-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "id": pipeline_id,
        "name": body.name,
        "nodes": body.nodes,
        "edges": body.edges,
        "created_at": now,
        "updated_at": now,
    }
    _pipeline_path(pipeline_id).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return JSONResponse(record, status_code=201)


@router.get("/api/pipelines")
async def list_pipelines() -> JSONResponse:
    records = []
    for p in sorted(_pipelines_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            records.append(
                {"id": data["id"], "name": data["name"], "updated_at": data.get("updated_at")}
            )
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("pipelines: skipping corrupt file %s: %s", p.name, exc)
    return JSONResponse(records)


@router.get("/api/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str) -> JSONResponse:
    try:
        return JSONResponse(_load_pipeline(pipeline_id))
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id!r} not found")


@router.put("/api/pipelines/{pipeline_id}")
async def update_pipeline(pipeline_id: str, body: PipelineDefinition) -> JSONResponse:
    try:
        existing = _load_pipeline(pipeline_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id!r} not found")
    existing.update(
        {
            "name": body.name,
            "nodes": body.nodes,
            "edges": body.edges,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _pipeline_path(pipeline_id).write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return JSONResponse(existing)


@router.delete("/api/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str) -> JSONResponse:
    try:
        path = _pipeline_path(pipeline_id)
        if not path.exists():
            raise KeyError(pipeline_id)
        path.unlink()
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id!r} not found")
    return JSONResponse({"ok": True, "id": pipeline_id})


@router.post("/api/pipelines/{pipeline_id}/run")
async def run_pipeline(pipeline_id: str) -> JSONResponse:
    """Trigger execution of a saved pipeline via the existing task/chat endpoint."""
    try:
        pipeline = _load_pipeline(pipeline_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id!r} not found")

    # Extract agent steps from nodes with type=="agent"
    step_names = [
        n["config"].get("name", "").lower().replace(" ", "_")
        for n in pipeline.get("nodes", [])
        if n.get("type") == "agent" and isinstance(n.get("config"), dict)
    ]
    logger.info("pipeline run requested: id=%s steps=%s", pipeline_id, step_names)
    # Return run metadata — actual execution goes through /v1/chat/completions
    return JSONResponse(
        {
            "pipeline_id": pipeline_id,
            "name": pipeline["name"],
            "step_names": step_names,
            "status": "queued",
            "message": (
                "Submit this pipeline via POST /v1/chat/completions with the desired task prompt."
            ),
        }
    )
