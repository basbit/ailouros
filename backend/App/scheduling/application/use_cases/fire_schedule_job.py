"""FireScheduleJobUseCase — fires a scheduled pipeline job.

Rules (INV-7): no fastapi/redis/httpx/openai/anthropic/langgraph at module level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.App.scheduling.domain.ports import ScheduleStorePort

logger = logging.getLogger(__name__)


@dataclass
class FireScheduleJobCommand:
    schedule_id: str
    override_prompt: Optional[str] = None


@dataclass
class FireScheduleJobResult:
    schedule_id: str
    status: str  # fired | skipped | failed
    task_id: Optional[str] = None
    error: Optional[str] = None


class FireScheduleJobUseCase:
    """Fire a scheduled pipeline job by schedule_id.

    Delegates actual pipeline execution to the injected ``pipeline_runner_fn``.
    Updates the schedule store with last_run timestamp on success.
    """

    def __init__(
        self,
        schedule_store: ScheduleStorePort,
        pipeline_runner_fn: Callable[[str, dict[str, Any]], str],
    ) -> None:
        self._store = schedule_store
        self._runner = pipeline_runner_fn

    def execute(self, cmd: FireScheduleJobCommand) -> FireScheduleJobResult:
        """Fire the schedule job and return a structured result.

        Args:
            cmd: FireScheduleJobCommand with schedule_id and optional prompt override.

        Returns:
            FireScheduleJobResult with status (fired | skipped | failed).
        """
        job = self._store.get_job(cmd.schedule_id)
        if not job:
            logger.warning(
                "FireScheduleJobUseCase: schedule %s not found — skipping",
                cmd.schedule_id,
            )
            return FireScheduleJobResult(
                schedule_id=cmd.schedule_id,
                status="skipped",
                error="schedule not found",
            )

        if not job.get("enabled", True):
            logger.info(
                "FireScheduleJobUseCase: schedule %s is disabled — skipping",
                cmd.schedule_id,
            )
            return FireScheduleJobResult(
                schedule_id=cmd.schedule_id,
                status="skipped",
                error="schedule disabled",
            )

        prompt = cmd.override_prompt or job.get("prompt", "")
        try:
            task_id = self._runner(prompt, job)
            self._store.update_job(
                cmd.schedule_id,
                last_run=datetime.now(timezone.utc).isoformat(),
            )
            logger.info(
                "FireScheduleJobUseCase: schedule %s fired → task %s",
                cmd.schedule_id,
                task_id,
            )
            return FireScheduleJobResult(
                schedule_id=cmd.schedule_id,
                status="fired",
                task_id=task_id,
            )
        except Exception as exc:
            msg = str(exc)
            logger.error(
                "FireScheduleJobUseCase: schedule %s failed: %s",
                cmd.schedule_id,
                msg,
            )
            return FireScheduleJobResult(
                schedule_id=cmd.schedule_id,
                status="failed",
                error=msg,
            )
