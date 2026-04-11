"""Tasks domain ports and value objects.

Rules (INV-7): this module MUST NOT import fastapi, redis, httpx, openai,
anthropic, langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

import re as _re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """Canonical task lifecycle statuses (replaces magic strings)."""
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_HUMAN = "awaiting_human"
    AWAITING_SHELL = "awaiting_shell"


@dataclass(frozen=True)
class TaskId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("TaskId.value must not be empty")
        if len(self.value) > 255:
            raise ValueError(f"TaskId.value too long: {len(self.value)} > 255")
        if not _re.match(r'^[\w\-]+$', self.value):
            raise ValueError(
                f"TaskId.value contains invalid characters: {self.value!r}. "
                "Only alphanumeric, hyphens, and underscores are allowed."
            )

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

class TaskStorePort(ABC):
    """Abstraction over task persistence (replaces direct redis/in-memory access)."""

    @abstractmethod
    def create_task(self, task_id: TaskId, initial_data: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_task(self, task_id: TaskId) -> dict[str, Any]: ...

    @abstractmethod
    def update_task(
        self,
        task_id: TaskId,
        *,
        status: Optional[TaskStatus] = None,
        agent: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None: ...
