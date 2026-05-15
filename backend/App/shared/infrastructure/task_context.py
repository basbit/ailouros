from __future__ import annotations

from typing import Optional


def bind_active_task(task_id: Optional[str]) -> None:
    from backend.App.integrations.infrastructure.observability.logging_config import (
        set_task_id,
    )
    from backend.App.shared.infrastructure.activity_recorder import set_active_task

    cleaned = task_id.strip() if isinstance(task_id, str) else ""
    set_task_id(cleaned)
    set_active_task(cleaned or None)


__all__ = ["bind_active_task"]
