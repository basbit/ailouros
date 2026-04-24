from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.App.shared.infrastructure.json_file_io import read_json_file, write_json_file

logger = logging.getLogger(__name__)

_NOTES_FILE = Path(
    os.getenv(
        "SWARM_MEMORY_NOTES_FILE",
        str(Path(__file__).resolve().parents[4] / "var" / "memory_notes.json"),
    )
)
_lock = threading.Lock()


def _load_notes() -> list[dict[str, Any]]:
    if not _NOTES_FILE.is_file():
        return []
    return read_json_file(_NOTES_FILE)


def _save_notes(notes: list[dict[str, Any]]) -> None:
    _NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json_file(_NOTES_FILE, notes)


def list_memory_notes() -> dict[str, Any]:
    with _lock:
        notes = _load_notes()
    return {"entries": notes}


def append_memory_note(*, text: str, source: str = "") -> dict[str, Any]:
    if not text.strip():
        raise ValueError("text must not be empty")
    entry: dict[str, Any] = {
        "text": text.strip(),
        "source": source,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _lock:
        notes = _load_notes()
        notes.append(entry)
        _save_notes(notes)
    logger.info("memory_notes: appended note (source=%r)", source)
    return entry


def delete_memory_note(note_index: int) -> dict[str, Any]:
    with _lock:
        notes = _load_notes()
        if note_index < 0 or note_index >= len(notes):
            raise IndexError(f"note index {note_index} out of range (have {len(notes)})")
        removed = notes.pop(note_index)
        _save_notes(notes)
    logger.info("memory_notes: deleted note at index %d", note_index)
    return {"deleted": removed}


async def consolidate_memory_notes() -> dict[str, Any]:
    from backend.App.integrations.application.memory_consolidation import MemoryConsolidator

    def _run() -> dict[str, Any]:
        consolidator = MemoryConsolidator()
        stats = consolidator.run_consolidation()
        return {"status": "ok", "stats": stats}

    return await asyncio.to_thread(_run)
