"""ScheduleStoreAdapter — in-memory schedule storage backed by controllers/schedules globals.

Strangler Fig: delegates to the in-memory _schedule_store dict owned by
backend.UI.REST.controllers.schedules so the new DDD port interface
is available without Redis.

Rules (INV-7): this is infrastructure — may import external deps.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.scheduling.domain.ports import ScheduleStorePort

logger = logging.getLogger(__name__)


class ScheduleStoreAdapter(ScheduleStorePort):
    """Infrastructure adapter: delegates to in-memory schedule store globals."""

    def get_job(self, schedule_id: str) -> Optional[dict[str, Any]]:
        from backend.UI.REST.controllers.schedules import _schedule_store, _schedule_lock
        with _schedule_lock:
            return dict(_schedule_store[schedule_id]) if schedule_id in _schedule_store else None

    def update_job(self, schedule_id: str, **kwargs: Any) -> None:
        from backend.UI.REST.controllers.schedules import _schedule_store, _schedule_lock
        with _schedule_lock:
            if schedule_id in _schedule_store:
                _schedule_store[schedule_id].update(kwargs)

    def list_jobs(self) -> list[dict[str, Any]]:
        from backend.UI.REST.controllers.schedules import _schedule_store, _schedule_lock
        with _schedule_lock:
            return [dict(j) for j in _schedule_store.values()]
