"""Scheduling domain ports.

Rules (INV-7): this module MUST NOT import fastapi, redis, httpx, openai,
anthropic, langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class ScheduleStorePort(ABC):
    """Port for schedule job persistence (INV-7: no external deps)."""

    @abstractmethod
    def get_job(self, schedule_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def update_job(self, schedule_id: str, **kwargs: Any) -> None: ...

    @abstractmethod
    def list_jobs(self) -> list[dict[str, Any]]: ...
