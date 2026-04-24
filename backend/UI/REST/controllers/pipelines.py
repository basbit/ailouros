from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.App.orchestration.application.pipeline import pipeline_store

router = APIRouter()
_PIPELINES_DIR = pipeline_store.DEFAULT_PIPELINES_DIR


class PipelineDefinition(BaseModel):
    name: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]

    def to_application(self) -> pipeline_store.PipelineDefinition:
        return pipeline_store.PipelineDefinition(
            name=self.name,
            nodes=self.nodes,
            edges=self.edges,
        )


@router.post("/api/pipelines")
async def create_pipeline(body: PipelineDefinition) -> JSONResponse:
    record = pipeline_store.create_pipeline(body.to_application(), _PIPELINES_DIR)
    return JSONResponse(record, status_code=201)


@router.get("/api/pipelines")
async def list_pipelines() -> JSONResponse:
    return JSONResponse(pipeline_store.list_pipelines(_PIPELINES_DIR))


@router.get("/api/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str) -> JSONResponse:
    try:
        return JSONResponse(pipeline_store.get_pipeline(pipeline_id, _PIPELINES_DIR))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=404, detail=f"Pipeline {pipeline_id!r} not found"
        )


@router.put("/api/pipelines/{pipeline_id}")
async def update_pipeline(pipeline_id: str, body: PipelineDefinition) -> JSONResponse:
    try:
        record = pipeline_store.update_pipeline(
            pipeline_id,
            body.to_application(),
            _PIPELINES_DIR,
        )
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=404, detail=f"Pipeline {pipeline_id!r} not found"
        )
    return JSONResponse(record)


@router.delete("/api/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str) -> JSONResponse:
    try:
        return JSONResponse(pipeline_store.delete_pipeline(pipeline_id, _PIPELINES_DIR))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=404, detail=f"Pipeline {pipeline_id!r} not found"
        )
