"""P0-10: Contract Validator — single source of truth for inter-agent messages.

Validates:
- Message schema (type, id, required fields)
- Evidence enforcement (RESPONSE requires non-empty evidence)
- Error enforcement (ERROR requires non-empty errors)
- State transitions (PENDING → IN_PROGRESS → DONE / ERROR)
- Execution limits (P0-11: max messages, max depth, max parallel tasks)

Violation → hard ERROR, no silent retries/fallbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import uuid
from dataclasses import dataclass
from typing import Any, Optional, TypedDict, cast

_logger = logging.getLogger(__name__)

# Valid message types per protocol §6.1
VALID_MESSAGE_TYPES = frozenset({"REQUEST", "RESPONSE", "EVENT", "ERROR"})

# Valid task states per §11.2
VALID_TASK_STATES = frozenset({"PENDING", "IN_PROGRESS", "DONE", "ERROR"})

# Allowed state transitions per §11.2
_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"IN_PROGRESS", "ERROR"}),
    "IN_PROGRESS": frozenset({"DONE", "ERROR"}),
    "DONE": frozenset(),       # terminal
    "ERROR": frozenset(),      # terminal
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


class ContractViolation(Exception):
    """Raised when an inter-agent message violates the protocol contract."""

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
    """Validates inter-agent messages per the Swarm Inter-Agent Contract (§6.1).

    Maintains per-task state: seen message IDs, task states, message counts,
    and parent depth tracking.

    Usage:
        validator = ContractValidator()
        validator.validate_outgoing(message)   # before sending
        validator.validate_incoming(message)    # before processing
    """

    def __init__(
        self,
        *,
        limits: ContractValidatorLimits | None = None,
        evidence_version: str = "",
    ) -> None:
        # task_id → set of seen message ids (for dedup, §11.3)
        self._seen_ids: dict[str, set[str]] = {}
        # task_id → current state
        self._task_states: dict[str, str] = {}
        # task_id → message count (for §11.4 limits)
        self._message_counts: dict[str, int] = {}
        # task_id → task_owner (§6.1.2)
        self._task_owners: dict[str, str] = {}
        # branch_id → state (for branch-level tracking)
        self._branch_states: dict[str, str] = {}
        # Currently active parallel tasks count
        self._active_tasks: set[str] = set()
        # Per-step state tracking: (task_id, step_id) → state
        self._step_states: dict[tuple[str, str], str] = {}
        self._limits = limits or ContractValidatorLimits()
        self._evidence_version = evidence_version
        # §4 Evidence coverage map: task_id → list of evidence items collected
        self._evidence_map: dict[str, list[dict[str, Any]]] = {}
        # Metrics: total evidence items seen / items without traceable source
        self._evidence_total: int = 0
        self._evidence_hallucinated: int = 0

    def validate_outgoing(self, msg: ProtocolMessage) -> None:
        """Validate a message before sending. Raises ContractViolation on failure."""
        self._validate_schema(msg)
        self._validate_limits(msg)
        self._validate_evidence(msg)
        self._validate_assumptions(msg)
        self._validate_errors(msg)
        self._register_message(msg)

    def validate_incoming(self, msg: ProtocolMessage) -> None:
        """Validate a message before processing. Raises ContractViolation on failure."""
        self._validate_schema(msg)
        self._validate_dedup(msg)
        self._validate_limits(msg)
        self._validate_evidence(msg)
        self._validate_assumptions(msg)
        self._validate_errors(msg)
        self._validate_state_transition(msg)
        self._register_message(msg)

    def get_task_state(self, task_id: str) -> str:
        """Return current state for a task."""
        return self._task_states.get(task_id, "PENDING")

    def transition_task(self, task_id: str, new_state: str) -> None:
        """Explicitly transition a task to a new state.

        Raises ContractViolation if the transition is not allowed.
        """
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
        """Mark a pipeline step as IN_PROGRESS (§11.2 per-step state)."""
        key = (task_id, step_id)
        current = self._step_states.get(key, "PENDING")
        if current in ("DONE", "ERROR"):
            _logger.warning(
                "ContractValidator: step %s/%s already in terminal state %s, skipping start",
                task_id, step_id, current,
            )
            return
        self._step_states[key] = "IN_PROGRESS"
        # Also ensure task is IN_PROGRESS
        if self._task_states.get(task_id) == "PENDING":
            self._task_states[task_id] = "IN_PROGRESS"

    def step_complete(self, task_id: str, step_id: str) -> None:
        """Mark a pipeline step as DONE (§11.2 per-step state)."""
        key = (task_id, step_id)
        self._step_states[key] = "DONE"

    def step_error(self, task_id: str, step_id: str, error_msg: str = "") -> None:
        """Mark a pipeline step as ERROR (§11.2 per-step state)."""
        key = (task_id, step_id)
        self._step_states[key] = "ERROR"
        _logger.error(
            "ContractValidator: step %s/%s → ERROR: %s", task_id, step_id, error_msg,
        )

    def get_step_state(self, task_id: str, step_id: str) -> str:
        """Return current state of a pipeline step."""
        return self._step_states.get((task_id, step_id), "PENDING")

    def register_task(self, task_id: str, owner: str) -> None:
        """Register a new task with its owner."""
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
        """Return runtime metrics for observability.

        Exposed via GET /v1/circuit-breakers and similar endpoints.
        Metrics:
          - hallucinated_evidence_pct: % of evidence items with no source/ref
          - active_tasks: number of currently in-flight tasks
          - tracked_tasks: total tasks registered lifetime
          - total_messages: sum of all per-task message counts
        """
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
        """Return all evidence items collected for a task (coverage map)."""
        return list(self._evidence_map.get(task_id, []))

    # --- internal validation methods ---

    def _validate_schema(self, msg: ProtocolMessage) -> None:
        """§6.1 §11.1: Validate message schema."""
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
        """§4, §11.1: RESPONSE requires non-empty evidence with traceable provenance.

        Also validates:
        - Each item must have 'source' or 'ref' (provenance).  Items without both
          are counted as hallucinated evidence and logged as warnings.
        - Evidence items with 'derived: true' must include a 'ref' back to the
          primary evidence they were derived from.
        - Collects evidence into _evidence_map for per-task coverage tracking.
        """
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
            # Derived-field check: derived items must carry a ref
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

        # Accumulate into coverage map.  Cast widens TypedDict →
        # ``dict[str, Any]`` for the mutable map; TypedDict is not a subtype
        # of ``dict[str, Any]`` in mypy even though they share the runtime.
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
        """§4: RESPONSE must not carry unresolved lifecycle assumptions.

        An assumption is unresolved when it explicitly carries ``"resolved": false``.
        These represent open questions that should have been answered before the
        response was emitted. Unresolved assumptions are logged as warnings; a
        future stricter policy can raise ContractViolation instead.
        """
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
        """§6, §11.1: ERROR requires non-empty errors."""
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
        """§11.3: Reject duplicate message IDs per task."""
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
        """§11.4 / P0-11: Enforce execution limits."""
        task_id = (msg.get("context") or {}).get("task_id", "")
        # Max messages per task
        count = self._message_counts.get(task_id, 0)
        max_msgs = self._limits.max_messages_per_task
        if count >= max_msgs:
            raise ContractViolation(
                code="MAX_MESSAGES_EXCEEDED",
                message=f"Task {task_id} exceeded max messages ({max_msgs})",
                context={"task_id": task_id, "count": count, "limit": max_msgs},
            )
        # Max parent depth
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
        # Max parallel tasks (only on REQUEST with parent_id=null → new task)
        if msg.get("type") == "REQUEST" and not parent_id:
            max_parallel = self._limits.max_parallel_tasks
            if self.active_task_count() >= max_parallel:
                raise ContractViolation(
                    code="MAX_PARALLEL_TASKS_EXCEEDED",
                    message=f"Active tasks ({self.active_task_count()}) at limit ({max_parallel})",
                    context={"active": self.active_task_count(), "limit": max_parallel},
                )

    def _validate_state_transition(self, msg: ProtocolMessage) -> None:
        """§11.2: Validate that the message implies a valid state transition."""
        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_type = msg.get("type")
        current_state = self._task_states.get(task_id)

        if current_state in ("DONE", "ERROR"):
            raise ContractViolation(
                code="TERMINAL_STATE",
                message=f"Task {task_id} is in terminal state {current_state}, no further messages allowed",
                context={"task_id": task_id, "state": current_state, "message_type": msg_type},
            )

        # REQUEST on PENDING task → transition to IN_PROGRESS
        if msg_type == "REQUEST" and current_state == "PENDING":
            self._task_states[task_id] = "IN_PROGRESS"

        # ERROR → terminal
        if msg_type == "ERROR" and current_state:
            self._task_states[task_id] = "ERROR"
            self._active_tasks.discard(task_id)

    def _register_message(self, msg: ProtocolMessage) -> None:
        """Track the message for dedup and counting."""
        task_id = (msg.get("context") or {}).get("task_id", "")
        msg_id = msg.get("id", "")
        if task_id not in self._seen_ids:
            self._seen_ids[task_id] = set()
        self._seen_ids[task_id].add(msg_id)
        self._message_counts[task_id] = self._message_counts.get(task_id, 0) + 1

    def _compute_depth(self, msg: ProtocolMessage) -> int:
        """Estimate parent chain depth from context.parent_id.

        Since we don't store full message graph, we use a simple counter
        based on the task's total message count as a proxy.
        In a full implementation, this would walk the parent_id chain.
        """
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
        """Build a protocol-compliant ERROR message.

        Cast widens the concrete dict to ``ProtocolMessage``; the ``"from"``
        key is part of the wire protocol but is not representable as an
        identifier in Python, so the TypedDict uses ``from_`` and the
        return cast reconciles the two.
        """
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


# Module-level singleton for use across the orchestrator
_global_validator: Optional[ContractValidator] = None


def get_validator() -> ContractValidator:
    """Return the global ContractValidator singleton."""
    global _global_validator
    if _global_validator is None:
        _global_validator = ContractValidator()
    return _global_validator


def set_validator(validator: ContractValidator) -> None:
    """Set the global validator instance."""
    global _global_validator
    _global_validator = validator


def reset_validator() -> None:
    """Reset the global validator (for testing)."""
    global _global_validator
    _global_validator = None


# ---------------------------------------------------------------------------
# §10.3-7: Evidence normalization helpers
# ---------------------------------------------------------------------------

def normalize_evidence(evidence: ProtocolEvidence) -> ProtocolEvidence:
    """Add optional hash/preview/size fields to an evidence entry.

    Used for comparison, caching, and diagnostics per §4.2 extension.

    Adds (if not already present):
    - ``hash``: sha256 of ``data`` field
    - ``preview``: first 120 chars of ``data``
    - ``size``: byte length of ``data``
    """
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
    """Normalize all entries in an evidence list."""
    return [normalize_evidence(e) for e in evidence_list]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Validate the orchestrator<->agent transport boundary.

    The runtime still exchanges plain prompt/output strings, so this helper
    wraps them in protocol-compliant REQUEST/RESPONSE envelopes and validates
    both directions at the transport boundary.
    """
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
