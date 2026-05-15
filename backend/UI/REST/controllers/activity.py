from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.App.shared.infrastructure.activity_recorder import (
    available_channels,
    read_tail,
)

router = APIRouter()


@router.get("/v1/tasks/{task_id}/activity/{channel}")
def get_activity_tail(
    task_id: str,
    channel: str,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    task_clean = task_id.strip()
    channel_clean = channel.strip()
    if not task_clean:
        raise HTTPException(status_code=400, detail="task_id must not be blank")
    if channel_clean not in available_channels():
        raise HTTPException(
            status_code=404,
            detail=(
                f"unknown channel {channel_clean!r}; "
                f"available: {list(available_channels())}"
            ),
        )
    entries = read_tail(task_clean, channel_clean, limit=limit)
    return {
        "task_id": task_clean,
        "channel": channel_clean,
        "limit": limit,
        "count": len(entries),
        "entries": entries,
    }


@router.get("/v1/tasks/{task_id}/activity")
def list_activity_channels(task_id: str) -> dict[str, Any]:
    if not task_id.strip():
        raise HTTPException(status_code=400, detail="task_id must not be blank")
    return {"task_id": task_id.strip(), "channels": list(available_channels())}


__all__ = ["router"]
