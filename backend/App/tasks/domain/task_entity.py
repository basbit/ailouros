from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from backend.App.shared.application.datetime_utils import utc_now_iso
from backend.App.shared.domain.exceptions import DomainError
from backend.App.tasks.domain.ports import TaskStatus


class InvalidTaskTransitionError(DomainError):
    pass


@dataclass
class TaskEvent:
    timestamp: str
    agent: str
    message: str
    event_type: str

    @classmethod
    def now(cls, agent: str, message: str, event_type: str) -> "TaskEvent":
        return cls(
            timestamp=utc_now_iso(),
            agent=agent,
            message=message,
            event_type=event_type,
        )


@dataclass
class Task:
    task_id: str
    prompt: str
    status: TaskStatus
    agents: list[str] = field(default_factory=list)
    history: list[TaskEvent] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    version: int = 1

    _VALID_TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "in_progress": frozenset(["completed", "failed", "cancelled", "awaiting_human", "awaiting_shell"]),
        "awaiting_human": frozenset(["in_progress", "cancelled"]),
        "awaiting_shell": frozenset(["in_progress", "cancelled"]),
        "failed": frozenset(["in_progress"]),
        "completed": frozenset(),
        "cancelled": frozenset(),
    }

    def can_transition_to(self, new_status: "TaskStatus") -> bool:
        current = self.status.value if hasattr(self.status, "value") else str(self.status)
        new = new_status.value if hasattr(new_status, "value") else str(new_status)
        return new in self._VALID_TRANSITIONS.get(current, frozenset())

    def transition_to(self, new_status: "TaskStatus") -> None:
        if not self.can_transition_to(new_status):
            raise InvalidTaskTransitionError(
                f"Invalid transition: {self.status} → {new_status}"
            )
        self.status = new_status

    def append_event(self, event: TaskEvent) -> None:
        self.history.append(event)
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "status": self.status.value,
            "agents": list(self.agents),
            "history": [vars(e) for e in self.history],
            "created_at": self.created_at,
            "version": self.version,
        }
