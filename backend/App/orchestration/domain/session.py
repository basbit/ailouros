"""Durable agent session domain model — R1.1 Durable agent sessions.

Rules (INV-7): MUST NOT import fastapi, redis, httpx, openai, anthropic,
langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMING = "resuming"


@dataclass
class SessionCheckpoint:
    checkpoint_id: str
    session_id: str
    step_name: str
    state_snapshot: dict[str, Any]
    created_at: str                         # ISO-8601


@dataclass
class AgentSession:
    session_id: str
    task_id: str
    status: SessionStatus
    created_at: str                         # ISO-8601
    updated_at: str                         # ISO-8601
    last_checkpoint_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
