"""Wiki knowledge graph API.

GET /api/wiki/graph  — auto-build (or serve cached) wiki/graph.json
GET /api/wiki/file   — return raw markdown for a wiki file (path query param)

Optional query param ``workspace_root`` for both endpoints:
  - If provided: wiki is at ``<workspace_root>/.swarm/wiki/``
  - If not provided: use the app's own ``_WIKI_ROOT``
  - Security: workspace_root must not escape outside ``SWARM_WORKSPACE_BASE``
    env var when set.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# Wiki lives in .swarm/wiki/ inside the app directory
_BACKEND_ROOT = Path(__file__).resolve().parents[3]  # app/
_WIKI_ROOT = _BACKEND_ROOT / ".swarm" / "wiki"


def _resolve_wiki_root(workspace_root: Optional[str]) -> Path:
    """Return the wiki root for the given workspace_root param.

    If workspace_root is None or empty, returns the app's own _WIKI_ROOT.
    Validates that workspace_root doesn't escape SWARM_WORKSPACE_BASE when set.
    """
    if not workspace_root or not workspace_root.strip():
        return _WIKI_ROOT

    requested = Path(workspace_root.strip()).resolve()

    # Security: if SWARM_WORKSPACE_BASE is set, workspace_root must live under it
    base_env = os.getenv("SWARM_WORKSPACE_BASE", "").strip()
    if base_env:
        base = Path(base_env).resolve()
        try:
            requested.relative_to(base)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"workspace_root must be inside SWARM_WORKSPACE_BASE ({base})",
            ) from exc

    return requested / ".swarm" / "wiki"


@router.get("/api/wiki/graph")
async def get_wiki_graph(
    workspace_root: Optional[str] = Query(None, description="Optional project root to use instead of default wiki"),
    force: bool = Query(False, description="Force rebuild (ignore cached graph.json)"),
) -> JSONResponse:
    """Auto-build (or serve cached) wiki/graph.json for the knowledge graph UI."""
    from backend.App.workspace.application.wiki_service import get_or_build_graph, build_wiki_graph

    wiki_root = _resolve_wiki_root(workspace_root)
    try:
        if force:
            import json as _json
            data = await asyncio.to_thread(build_wiki_graph, wiki_root)
            # Persist so subsequent non-force requests get the new graph
            graph_file = wiki_root / "graph.json"
            try:
                graph_file.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        else:
            data = await asyncio.to_thread(get_or_build_graph, wiki_root)
    except Exception as exc:
        logger.exception("wiki graph build failed for %s", wiki_root)
        raise HTTPException(status_code=500, detail=f"Failed to build wiki graph: {exc}") from exc
    return JSONResponse(data)


@router.get("/api/wiki/file")
async def get_wiki_file(
    path: str = Query(..., description="Relative path inside wiki/, e.g. 'features/chat'"),
    workspace_root: Optional[str] = Query(None, description="Optional project root to use instead of default wiki"),
) -> PlainTextResponse:
    """Return raw Markdown content for a wiki file.

    The *path* may omit the .md extension.
    """
    wiki = _resolve_wiki_root(workspace_root)
    wiki.mkdir(parents=True, exist_ok=True)

    # Normalise: strip leading slash, add .md if needed
    clean = path.lstrip("/")
    if not clean.endswith(".md"):
        clean += ".md"

    target = (wiki / clean).resolve()
    # Path traversal guard
    try:
        target.relative_to(wiki)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path outside wiki root") from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Wiki file not found: {path!r}")

    return PlainTextResponse(target.read_text(encoding="utf-8"), media_type="text/markdown")
