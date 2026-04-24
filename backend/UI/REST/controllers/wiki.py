from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.App.workspace.application.wiki_service import (
    get_or_build_graph,
    read_wiki_file,
    rebuild_and_cache_graph,
    resolve_wiki_root,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/wiki/graph")
async def get_wiki_graph(
    workspace_root: Optional[str] = Query(
        None, description="Optional project root to use instead of default wiki"
    ),
    force: bool = Query(False, description="Force rebuild (ignore cached graph.json)"),
) -> JSONResponse:
    try:
        wiki_root = resolve_wiki_root(workspace_root)
        builder = rebuild_and_cache_graph if force else get_or_build_graph
        data = await asyncio.to_thread(builder, wiki_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("wiki graph build failed")
        raise HTTPException(
            status_code=500, detail=f"Failed to build wiki graph: {exc}"
        ) from exc
    return JSONResponse(data)


@router.get("/api/wiki/file")
async def get_wiki_file(
    path: str = Query(
        ..., description="Relative path inside wiki/, e.g. 'features/chat'"
    ),
    workspace_root: Optional[str] = Query(
        None, description="Optional project root to use instead of default wiki"
    ),
) -> PlainTextResponse:
    try:
        wiki_root = resolve_wiki_root(workspace_root)
        content = read_wiki_file(wiki_root, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(content, media_type="text/markdown")
