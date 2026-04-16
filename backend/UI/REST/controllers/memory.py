"""Memory routes: /v1/memory/notes, /v1/memory/consolidate."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/v1/memory/notes")
async def get_memory_notes() -> JSONResponse:
    """Return the last 50 entries from the pattern-memory notes file."""
    notes_path_str = os.getenv("SWARM_MEMORY_NOTES_PATH", "").strip()
    notes_path = (
        Path(notes_path_str).expanduser().resolve()
        if notes_path_str
        else (Path.cwd() / ".swarm" / "memory_notes.jsonl").resolve()
    )

    if not notes_path.is_file():
        return JSONResponse(content={"entries": []})

    entries: list[dict[str, Any]] = []
    try:
        for line in notes_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("text"):
                    entries.append(obj)
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        return JSONResponse(content={"entries": [], "error": str(exc)})

    return JSONResponse(content={"entries": entries[-50:][::-1]})


@router.post("/v1/memory/notes")
async def post_memory_note(request: Request) -> JSONResponse:
    """Append a note to the memory_notes.jsonl file."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="'text' field is required and must be non-empty")

    source = str(body.get("source") or "").strip()

    notes_path_str = os.getenv("SWARM_MEMORY_NOTES_PATH", "").strip()
    notes_path = (
        Path(notes_path_str).expanduser().resolve()
        if notes_path_str
        else (Path.cwd() / ".swarm" / "memory_notes.jsonl").resolve()
    )

    entry: dict[str, Any] = {"text": text, "ts": time.time()}
    if source:
        entry["source"] = source

    try:
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        with notes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write note: {exc}") from exc

    return JSONResponse({"ok": True})


@router.delete("/v1/memory/notes/{idx}")
async def delete_memory_note(idx: int) -> JSONResponse:
    """Delete a single memory note by its display index (0 = most recent)."""
    notes_path_str = os.getenv("SWARM_MEMORY_NOTES_PATH", "").strip()
    notes_path = (
        Path(notes_path_str).expanduser().resolve()
        if notes_path_str
        else (Path.cwd() / ".swarm" / "memory_notes.jsonl").resolve()
    )

    if not notes_path.is_file():
        raise HTTPException(status_code=404, detail="No notes file found")

    lines = [ln for ln in notes_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # reverse index: GET returns reversed, so display idx=0 → file line = len-1
    file_idx = len(lines) - 1 - idx
    if file_idx < 0 or file_idx >= len(lines):
        raise HTTPException(status_code=404, detail=f"Note index {idx} out of range")

    del lines[file_idx]
    notes_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return JSONResponse({"ok": True})


@router.post("/v1/memory/consolidate")
async def memory_consolidate() -> JSONResponse:
    """Trigger a 'dream pass': cluster memory_notes.jsonl entries and write consolidated summary."""
    import asyncio

    notes_path_str = os.getenv("SWARM_MEMORY_NOTES_PATH", "").strip()
    notes_path = (
        Path(notes_path_str).expanduser().resolve()
        if notes_path_str
        else (Path.cwd() / ".swarm" / "memory_notes.jsonl").resolve()
    )

    entries: list[dict[str, Any]] = []
    if notes_path.is_file():
        try:
            for line in notes_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("text"):
                        entries.append(obj)
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            return JSONResponse(content={"status": "error", "error": str(exc), "entries_processed": 0})

    try:
        from backend.App.integrations.application.memory_consolidation import MemoryConsolidator
        consolidator = MemoryConsolidator(llm_backend=None)
        stats = await asyncio.to_thread(consolidator.run_consolidation)
        return JSONResponse(content={
            "status": "ok",
            "entries_processed": stats.get("episodes_loaded", 0),
            "stats": stats,
        })
    except ImportError:
        pass

    grouped: dict[str, list[str]] = {}
    for entry in entries:
        ns = str(entry.get("namespace") or "default")
        grouped.setdefault(ns, []).append(str(entry.get("text", "")))

    consolidated_path = notes_path.parent / "memory_notes_consolidated.jsonl"
    try:
        notes_path.parent.mkdir(parents=True, exist_ok=True)
        lines_out = [
            json.dumps({
                "namespace": ns,
                "text": "\n---\n".join(texts),
                "consolidated": True,
                "source_count": len(texts),
            }, ensure_ascii=False)
            for ns, texts in grouped.items()
        ]
        consolidated_path.write_text(
            "\n".join(lines_out) + ("\n" if lines_out else ""), encoding="utf-8"
        )
    except OSError as exc:
        return JSONResponse(content={"status": "error", "error": str(exc), "entries_processed": len(entries)})

    return JSONResponse(content={"status": "ok", "entries_processed": len(entries)})
