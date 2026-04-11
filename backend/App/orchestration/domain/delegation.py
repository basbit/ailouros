"""Native multi-agent delegation domain model — R1.5.

Rules (INV-7): MUST NOT import fastapi, redis, httpx, openai, anthropic,
langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DelegationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MergePolicy(str, Enum):
    FIRST_SUCCESS = "first_success"    # winner-takes-all parallel
    ALL_REQUIRED = "all_required"      # join: all branches must complete
    BEST_SCORE = "best_score"          # highest confidence wins
    SUPERVISOR = "supervisor"          # supervisor agent picks winner


@dataclass
class DelegationRequest:
    """A request to spawn a sub-agent for a bounded task."""
    delegation_id: str
    parent_session_id: str
    parent_task_id: str
    role: str                           # agent role to delegate to
    task_description: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)   # budget, timeout, scope
    merge_policy: MergePolicy = MergePolicy.ALL_REQUIRED
    created_at: str = ""


@dataclass
class DelegationResult:
    """Result returned by a delegated sub-agent."""
    delegation_id: str
    status: DelegationStatus
    output: str
    artifacts: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    elapsed_sec: float = 0.0
    completed_at: str = ""
    error: str | None = None


@dataclass
class DelegationBranch:
    """A parallel execution branch spawned by a delegation."""
    branch_id: str
    delegation_id: str
    session_id: str
    status: DelegationStatus = DelegationStatus.PENDING
    result: DelegationResult | None = None
