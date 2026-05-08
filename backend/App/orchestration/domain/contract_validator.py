
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional, cast

from backend.App.shared.application.datetime_utils import utc_now_iso
from backend.App.shared.domain.exceptions import DomainError
from backend.App.orchestration.domain._contract_protocol_types import (
    STATE_TRANSITIONS as _STATE_TRANSITIONS,
    VALID_MESSAGE_TYPES,
    ProtocolEvidence,
    ProtocolMessage,
)

_logger = logging.getLogger(__name__)


class ContractViolation(DomainError):

    def __init__(self, code: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.error_context = context or {}


@dataclass(frozen=True)
class ContractValidatorLimits:
    max_messages_per_task: int = 500
    max_parent_depth: int = 50
    max_parallel_tasks: int = 20


class ContractValidator:

    def __init__(
        self,
        *,
        limits: ContractValidatorLimits | None = None,
        evidence_version: str = "",
    ) -> None:
        self._seen_ids: dict[str, set[str]] = {}
        self._task_states: dict[str, str] = {}
        self._message_counts: dict[str, int] = {}
        self._task_owners: dict[str, str] = {}
        self._branch_states: dict[str, str] = {}
        self._active_tasks: set[str] = set()
        self._step_states: dict[tuple[str, str], str] = {}
        self._limits = limits or ContractValidatorLimits()
        self._evidence_version = evidence_version
        self._evidence_map: dict[str, list[dict[str, Any]]] = {}
        self._evidence_total: int = 0
        self._evidence_hallucinated: int = 0

    def validate_outgoing(self, msg: ProtocolMessage) -> None:
        self._validate_schema(msg)
        self._validate_limits(msg)
        self._validate_evidence(msg)
        self._validate_assumptions(msg)
        self._validate_errors(msg)
        self._register_message(msg)

    def validate_incoming(self, msg: ProtocolMessage) -> None:
        self._validate_schema(msg)
        self._validate_dedup(msg)
        self._validate_limits(msg)
        self._validate_evidence(msg)
        self._validate_assumptions(msg)
        self._validate_errors(msg)
        self._validate_state_transition(msg)
        self._register_message(msg)

    def get_task_state(self, task_id: str) -> str:
        return self._task_states.get(task_id, "PENDING")

    def transition_task(self, task_id: str, new_state: str) -> None:
        current = self._task_states.get(task_id, "PENDING")
        allowed = _STATE_TRANSITIONS.get(current, frozenset())
        if new_state not in allowed:
            raise ContractViolation(
                code="INVALID_STATE_TRANSITION",
                message=f"Cannot transition task {task_id} from {current} to {new_state}",
                context={"task_id": task_id, "current_state": current, "requested_state": new_state},
            )
        self._task_states[task_id] = new_state
        _logger.info("ContractValidator: task %s transitioned %s → %s", task_id, current, new_state)
        if new_state in ("DONE", "ERROR"):
            self._active_tasks.discard(task_id)

    def step_start(self, task_id: str, step_id: str) -> None:
        key = (task_id, step_id)
        current = self._step_states.get(key, "PENDING")
        if current in ("DONE", "ERROR"):
            _logger.warning(
                "ContractValidator: step %s/%s already in terminal state %s, skipping start",
                task_id, step_id, current,
            )
            return
        self._step_states[key] = "IN_PROGRESS"
        if self._task_states.get(task_id) == "PENDING":
            self._task_states[task_id] = "IN_PROGRESS"

    def step_complete(self, task_id: str, step_id: str) -> None:
        key = (task_id, step_id)
        self._step_states[key] = "DONE"

    def step_error(self, task_id: str, step_id: str, error_msg: str = "") -> None:
        key = (task_id, step_id)
        self._step_states[key] = "ERROR"
        _logger.error(
            "ContractValidator: step %s/%s → ERROR: %s", task_id, step_id, error_msg,
        )

    def get_step_state(self, task_id: str, step_id: str) -> str:
        return self._step_states.get((task_id, step_id), "PENDING")

    def register_task(self, task_id: str, owner: str) -> None:
        if task_id in self._task_states:
            raise ContractViolation(
                code="DUPLICATE_TASK",
                message=f"Task {task_id} already registered",
                context={"task_id": task_id},
            )
        self._task_states[task_id] = "PENDING"
        self._task_owners[task_id] = owner
        self._seen_ids[task_id] = set()
        self._message_counts[task_id] = 0
        self._active_tasks.add(task_id)

    def active_task_count(self) -> int:
        return len(self._active_tasks)

    def stats(self) -> dict[str, Any]:
        total = self._evidence_total
        hallucinated = self._evidence_hallucinated
        hal_pct = round(hallucinated / total * 100, 1) if total else 0.0
        total_messages = sum(self._message_counts.values())
        return {
            "evidence_total": total,
            "evidence_hallucinated": hallucinated,
            "hallucinated_evidence_pct": hal_pct,
            "active_tasks": len(self._active_tasks),
            "tracked_tasks": len(self._task_states),
            "total_messages": total_messages,
        }

    def evidence_coverage(self, task_id: str) -> list[dict[str, Any]]:
        return list(self._evidence_map.get(task_id, []))

    def _validate_schema(self, msg: ProtocolMessage) -> None:
        msg_type = msg.get("type")
        if msg_type not in VALID_MESSAGE_TYPES:
            raise ContractViolation(
                code="INVALID_MESSAGE_TYPE",
                message=f"Message type must be one of {VALID_MESSAGE_TYPES}, got: {msg_type!r}",
                context={"type": msg_type},
            )
        if not msg.get("id"):
            raise ContractViolation(
                code="MISSING_MESSAGE_ID",
                message="Message must have a non-empty 'id' field",
            )
        ctx = msg.get("context")
        if not isinstance(ctx, dict) or not ctx.get("task_id"):
            raise ContractViolation(
                code="MISSING_TASK_CONTEXT",
                message="Message must have context.task_id",
                context={"context": ctx},
            )

    def _validate_evidence(self, msg: ProtocolMessage) -> None:
        if msg.get("type") != "RESPONSE":
            return
        evidence = msg.get("evidence")
        if not evidence or not isinstance(evidence, list) or len(evidence) == 0:
            raise ContractViolation(
                code="MISSING_EVIDENCE",
                message="RESPONSE message must include non-empty 'evidence' array",
                context={"message_id": msg.get("id")},
            )

        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_id = msg.get("id", "")
        hallucinated: list[int] = []

        for idx, item in enumerate(evidence):
            if not isinstance(item, dict):
                continue
            self._evidence_total += 1
            has_source = bool(item.get("source", ""))
            has_ref = bool(item.get("ref", ""))
            if item.get("derived") and not has_ref:
                _logger.warning(
                    "ContractValidator: evidence[%d] in msg %r is derived but has no ref "
                    "(task=%s) — possible hallucination",
                    idx, msg_id, task_id,
                )
            if not has_source and not has_ref:
                self._evidence_hallucinated += 1
                hallucinated.append(idx)
                _logger.warning(
                    "ContractValidator: evidence[%d] in msg %r has no source/ref "
                    "(task=%s) — unverifiable / hallucinated",
                    idx, msg_id, task_id,
                )

        if task_id:
            self._evidence_map.setdefault(task_id, []).extend(
                [cast(dict[str, Any], item) for item in evidence if isinstance(item, dict)]
            )

        if hallucinated:
            _logger.warning(
                "ContractValidator: %d/%d evidence items in msg %r lack provenance (task=%s)",
                len(hallucinated), len(evidence), msg_id, task_id,
            )

    def _validate_assumptions(self, msg: ProtocolMessage) -> None:
        if msg.get("type") != "RESPONSE":
            return
        assumptions = msg.get("assumptions") or []
        if not isinstance(assumptions, list):
            return
        unresolved = [
            a for a in assumptions
            if isinstance(a, dict) and a.get("resolved") is False
        ]
        if unresolved:
            task_id = (msg.get("context") or {}).get("task_id", "")
            _logger.warning(
                "ContractValidator: msg %r (task=%s) has %d unresolved assumption(s): %s",
                msg.get("id"), task_id, len(unresolved),
                [a.get("text", "<no text>") for a in unresolved],
            )

    def _validate_errors(self, msg: ProtocolMessage) -> None:
        if msg.get("type") != "ERROR":
            return
        errors = msg.get("errors")
        if not errors or not isinstance(errors, list) or len(errors) == 0:
            raise ContractViolation(
                code="MISSING_ERRORS",
                message="ERROR message must include non-empty 'errors' array",
                context={"message_id": msg.get("id")},
            )

    def _validate_dedup(self, msg: ProtocolMessage) -> None:
        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_id = msg.get("id", "")
        seen = self._seen_ids.get(task_id, set())
        if msg_id in seen:
            raise ContractViolation(
                code="DUPLICATE_MESSAGE",
                message=f"Duplicate message id {msg_id!r} for task {task_id}",
                context={"message_id": msg_id, "task_id": task_id},
            )

    def _validate_limits(self, msg: ProtocolMessage) -> None:
        task_id = (msg.get("context") or {}).get("task_id", "")
        count = self._message_counts.get(task_id, 0)
        max_msgs = self._limits.max_messages_per_task
        if count >= max_msgs:
            raise ContractViolation(
                code="MAX_MESSAGES_EXCEEDED",
                message=f"Task {task_id} exceeded max messages ({max_msgs})",
                context={"task_id": task_id, "count": count, "limit": max_msgs},
            )
        parent_id = (msg.get("context") or {}).get("parent_id")
        if parent_id:
            depth = self._compute_depth(msg)
            max_depth = self._limits.max_parent_depth
            if depth > max_depth:
                raise ContractViolation(
                    code="MAX_DEPTH_EXCEEDED",
                    message=f"Task {task_id} parent chain depth {depth} exceeds limit {max_depth}",
                    context={"task_id": task_id, "depth": depth, "limit": max_depth},
                )
        if msg.get("type") == "REQUEST" and not parent_id:
            max_parallel = self._limits.max_parallel_tasks
            if self.active_task_count() >= max_parallel:
                raise ContractViolation(
                    code="MAX_PARALLEL_TASKS_EXCEEDED",
                    message=f"Active tasks ({self.active_task_count()}) at limit ({max_parallel})",
                    context={"active": self.active_task_count(), "limit": max_parallel},
                )

    def _validate_state_transition(self, msg: ProtocolMessage) -> None:
        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_type = msg.get("type")
        current_state = self._task_states.get(task_id)

        if current_state in ("DONE", "ERROR"):
            raise ContractViolation(
                code="TERMINAL_STATE",
                message=f"Task {task_id} is in terminal state {current_state}, no further messages allowed",
                context={"task_id": task_id, "state": current_state, "message_type": msg_type},
            )

        if msg_type == "REQUEST" and current_state == "PENDING":
            self._task_states[task_id] = "IN_PROGRESS"

        if msg_type == "ERROR" and current_state:
            self._task_states[task_id] = "ERROR"
            self._active_tasks.discard(task_id)

    def _register_message(self, msg: ProtocolMessage) -> None:
        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_id = msg.get("id", "")
        if task_id not in self._seen_ids:
            self._seen_ids[task_id] = set()
        self._seen_ids[task_id].add(msg_id)
        self._message_counts[task_id] = self._message_counts.get(task_id, 0) + 1

    def _compute_depth(self, msg: ProtocolMessage) -> int:
        return self._message_counts.get(
            (msg.get("context") or {}).get("task_id", ""), 0
        )

    def build_error_message(
        self,
        code: str,
        message: str,
        task_id: str,
        *,
        from_agent: str = "validator",
        operation: str = "",
        recoverable: bool = False,
    ) -> ProtocolMessage:
        return cast(ProtocolMessage, {
            "id": f"msg-{uuid.uuid4().hex[:12]}",
            "type": "ERROR",
            "from": from_agent,
            "to": "orchestrator",
            "intent": f"contract_violation: {code}",
            "context": {
                "task_id": task_id,
                "parent_id": None,
                "step": "validation",
                "workflow": "contract_validator",
            },
            "input": {},
            "output": {},
            "evidence": [],
            "assumptions": [],
            "errors": [
                {
                    "code": code,
                    "message": message,
                    "context": {
                        "operation": operation or "validate_message",
                        "input": {},
                        "expected": "valid protocol message",
                        "actual": message,
                    },
                    "recoverable": recoverable,
                }
            ],
            "meta": {
                "timestamp": "",
                "confidence": 0.0,
                "attempt": 1,
                "max_attempts": 1,
                "requires_action": False,
            },
        })


_global_validator: Optional[ContractValidator] = None


def get_validator() -> ContractValidator:
    global _global_validator
    if _global_validator is None:
        _global_validator = ContractValidator()
    return _global_validator


def set_validator(validator: ContractValidator) -> None:
    global _global_validator
    _global_validator = validator


def reset_validator() -> None:
    global _global_validator
    _global_validator = None


def normalize_evidence(evidence: ProtocolEvidence) -> ProtocolEvidence:
    import hashlib

    data = evidence.get("data")
    if data is None:
        return evidence

    data_str = str(data)
    result = dict(evidence)

    if "hash" not in result:
        result["hash"] = hashlib.sha256(data_str.encode("utf-8", errors="replace")).hexdigest()
    if "preview" not in result:
        result["preview"] = data_str[:120].replace("\n", " ")
    if "size" not in result:
        result["size"] = len(data_str.encode("utf-8", errors="replace"))

    return cast(ProtocolEvidence, result)


def normalize_evidence_list(evidence_list: list[ProtocolEvidence]) -> list[ProtocolEvidence]:
    return [normalize_evidence(e) for e in evidence_list]


def _now_iso() -> str:
    return utc_now_iso()


def validate_agent_exchange(
    *,
    task_id: str,
    step_id: str,
    role: str,
    prompt: str,
    output: str,
    workflow: str = "pipeline",
    validator: ContractValidator | None = None,
) -> None:
    task_id = (task_id or "").strip()
    if not task_id:
        return

    validator = validator or get_validator()
    if task_id not in validator._task_states:
        try:
            validator.register_task(task_id, "orchestrator")
        except ContractViolation:
            pass

    branch_id = f"{task_id}:{step_id or role}"
    base_context = {
        "task_id": task_id,
        "parent_id": None,
        "task_owner": "orchestrator",
        "step_owner": role,
        "branch_id": branch_id,
        "step": step_id or role,
        "workflow": workflow,
    }
    request_msg = {
        "id": f"msg-{uuid.uuid4().hex[:12]}",
        "type": "REQUEST",
        "from": "orchestrator",
        "to": role,
        "intent": f"run:{role}",
        "context": base_context,
        "input": {"prompt": prompt},
        "output": {},
        "evidence": [],
        "assumptions": [],
        "errors": [],
        "meta": {
            "timestamp": _now_iso(),
            "confidence": 1.0,
            "attempt": 1,
            "max_attempts": 1,
            "timeout_ms": 0,
            "max_events_per_step": 0,
            "seq": 0,
            "resources": {
                "tokens_total": 0,
                "tool_calls_total": 0,
                "execution_ms_total": 0,
            },
            "requires_action": True,
        },
    }
    validator.validate_outgoing(cast(ProtocolMessage, request_msg))

    response_msg = {
        "id": f"msg-{uuid.uuid4().hex[:12]}",
        "type": "RESPONSE",
        "from": role,
        "to": "orchestrator",
        "intent": f"result:{role}",
        "context": base_context,
        "input": {},
        "output": {
            "raw_output": output,
            "_evidence_map": {"raw_output": ["evidence[0]"]},
        },
        "evidence": normalize_evidence_list([
            {
                "source": "tool",
                "ref": f"agent_output:{role}",
                "data": output,
                "timestamp": _now_iso(),
                "version": validator._evidence_version,
            }
        ]),
        "assumptions": [],
        "errors": [],
        "meta": {
            "timestamp": _now_iso(),
            "confidence": 1.0,
            "attempt": 1,
            "max_attempts": 1,
            "timeout_ms": 0,
            "max_events_per_step": 0,
            "seq": 1,
            "resources": {
                "tokens_total": 0,
                "tool_calls_total": 0,
                "execution_ms_total": 0,
            },
            "requires_action": False,
        },
    }
    validator.validate_incoming(cast(ProtocolMessage, response_msg))
