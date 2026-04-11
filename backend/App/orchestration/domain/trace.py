"""Session trace domain model — R1.4 Product-level session tracing.

Rules (INV-7): MUST NOT import fastapi, redis, httpx, openai, anthropic,
langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    STEP_START = "step_start"
    STEP_END = "step_end"
    TOOL_CALL = "tool_call"
    GATE_OPEN = "gate_open"
    GATE_CLOSE = "gate_close"
    RETRY = "retry"
    HUMAN_APPROVAL = "human_approval"
    ARTIFACT = "artifact"
    ERROR = "error"


@dataclass
class TraceEvent:
    event_id: str
    trace_id: str
    session_id: str
    task_id: str
    step: str
    event_type: EventType
    timestamp: str                          # ISO-8601
    data: dict[str, Any] = field(default_factory=dict)
    parent_event_id: str | None = None


@dataclass
class TraceSession:
    session_id: str
    task_id: str
    run_id: str
    started_at: str                         # ISO-8601
    events: list[TraceEvent] = field(default_factory=list)
