
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
    FIRST_SUCCESS = "first_success"
    ALL_REQUIRED = "all_required"
    BEST_SCORE = "best_score"
    SUPERVISOR = "supervisor"


@dataclass
class DelegationRequest:
    delegation_id: str
    parent_session_id: str
    parent_task_id: str
    role: str
    task_description: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    merge_policy: MergePolicy = MergePolicy.ALL_REQUIRED
    created_at: str = ""


@dataclass
class DelegationResult:
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
    branch_id: str
    delegation_id: str
    session_id: str
    status: DelegationStatus = DelegationStatus.PENDING
    result: DelegationResult | None = None
