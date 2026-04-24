from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.App.integrations.application.memory_notes_service import (
    append_memory_note,
    consolidate_memory_notes,
    delete_memory_note,
    list_memory_notes,
)

router = APIRouter()


@router.get("/v1/memory/notes")
async def get_memory_notes() -> JSONResponse:
    try:
        return JSONResponse(content=list_memory_notes())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/memory/notes")
async def post_memory_note(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON body") from exc

    try:
        result = append_memory_note(
            text=str(body.get("text") or ""),
            source=str(body.get("source") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to write note: {exc}"
        ) from exc
    return JSONResponse(result)


@router.delete("/v1/memory/notes/{idx}")
async def remove_memory_note(idx: int) -> JSONResponse:
    try:
        return JSONResponse(delete_memory_note(idx))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/v1/memory/consolidate")
async def memory_consolidate() -> JSONResponse:
    try:
        return JSONResponse(content=await consolidate_memory_notes())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
