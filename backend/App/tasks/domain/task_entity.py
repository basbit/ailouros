"""Task domain entity and TaskEvent value object.

Rules (INV-7): domain layer — stdlib + typing only, no external deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from backend.App.tasks.domain.ports import TaskStatus


class InvalidTaskTransitionError(Exception):
    """Raised when a Task state transition is not permitted by the state machine."""


@dataclass
class TaskEvent:
    """Immutable event in a task's history."""

    timestamp: str  # ISO 8601
    agent: str
    message: str
    event_type: str  # step_start | step_end | status_change | error | human_gate | cancel

    @classmethod
    def now(cls, agent: str, message: str, event_type: str) -> "TaskEvent":
        """Create a TaskEvent timestamped at the current UTC time."""
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            message=message,
            event_type=event_type,
        )


@dataclass
class Task:
    """Task aggregate root."""

    task_id: str
    prompt: str
    status: TaskStatus
    agents: list[str] = field(default_factory=list)
    history: list[TaskEvent] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    version: int = 1

    # Valid state transitions: maps current status value → allowed next status values.
    _VALID_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "in_progress": frozenset(["completed", "failed", "cancelled", "awaiting_human", "awaiting_shell"]),
        "awaiting_human": frozenset(["in_progress", "cancelled"]),
        "awaiting_shell": frozenset(["in_progress", "cancelled"]),
        "failed": frozenset(["in_progress"]),  # retry
        "completed": frozenset(),
        "cancelled": frozenset(),
    }

    def can_transition_to(self, new_status: "TaskStatus") -> bool:
        """Return True if transitioning from current status to new_status is valid."""
        current = self.status.value if hasattr(self.status, "value") else str(self.status)
        new = new_status.value if hasattr(new_status, "value") else str(new_status)
        return new in self._VALID_TRANSITIONS.get(current, frozenset())

    def transition_to(self, new_status: "TaskStatus") -> None:
        """Transition to new_status, raising InvalidTaskTransitionError if not permitted."""
        if not self.can_transition_to(new_status):
            raise InvalidTaskTransitionError(
                f"Invalid transition: {self.status} → {new_status}"
            )
        self.status = new_status

    def append_event(self, event: TaskEvent) -> None:
        """Append an event to the task history and increment version."""
        self.history.append(event)
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize task to a plain dict for storage."""
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "status": self.status.value,
            "agents": list(self.agents),
            "history": [vars(e) for e in self.history],
            "created_at": self.created_at,
            "version": self.version,
        }
