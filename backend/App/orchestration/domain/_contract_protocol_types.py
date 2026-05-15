from __future__ import annotations

from typing import Any, TypedDict

VALID_MESSAGE_TYPES = frozenset({"REQUEST", "RESPONSE", "EVENT", "ERROR"})

VALID_TASK_STATES = frozenset({"PENDING", "IN_PROGRESS", "DONE", "ERROR"})

STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"IN_PROGRESS", "ERROR"}),
    "IN_PROGRESS": frozenset({"DONE", "ERROR"}),
    "DONE": frozenset(),
    "ERROR": frozenset(),
}


class ProtocolContext(TypedDict, total=False):
    task_id: str
    parent_id: str | None
    task_owner: str
    step_owner: str
    branch_id: str
    step: str
    workflow: str


class ProtocolEvidence(TypedDict, total=False):
    source: str
    ref: str
    data: str
    timestamp: str
    version: str
    hash: str
    preview: str
    size: int


class ProtocolErrorContext(TypedDict, total=False):
    operation: str
    input: dict[str, Any]
    expected: str
    actual: str


class ProtocolError(TypedDict, total=False):
    code: str
    message: str
    context: ProtocolErrorContext
    recoverable: bool


class ProtocolMessage(TypedDict, total=False):
    id: str
    type: str
    from_: str
    to: str
    intent: str
    context: ProtocolContext
    input: dict[str, Any]
    output: dict[str, Any]
    evidence: list[ProtocolEvidence]
    assumptions: list[dict[str, Any]]
    errors: list[ProtocolError]
    meta: dict[str, Any]


__all__ = (
    "VALID_MESSAGE_TYPES",
    "VALID_TASK_STATES",
    "STATE_TRANSITIONS",
    "ProtocolContext",
    "ProtocolEvidence",
    "ProtocolErrorContext",
    "ProtocolError",
    "ProtocolMessage",
)
