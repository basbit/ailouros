from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class Postmortem:
    id: str
    spec_id: str
    agent: str
    failure_kind: Literal["verifier_error", "retry_exhausted", "exception"]
    summary: str
    findings_excerpt: tuple[str, ...]
    recovery_attempted: str
    outcome: Literal["failed", "succeeded_after_retry"]
    recorded_at: datetime
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PostmortemQuery:
    spec_id: Optional[str] = None
    agent: Optional[str] = None
    failure_kind: Optional[str] = None
    tag: Optional[str] = None
    k: int = 5


_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "spec_id",
    "agent",
    "failure_kind",
    "summary",
    "findings_excerpt",
    "recovery_attempted",
    "outcome",
    "recorded_at",
    "tags",
)

_VALID_FAILURE_KINDS: frozenset[str] = frozenset(
    {"verifier_error", "retry_exhausted", "exception"}
)
_VALID_OUTCOMES: frozenset[str] = frozenset({"failed", "succeeded_after_retry"})


def serialise_postmortem(postmortem: Postmortem) -> dict[str, Any]:
    return {
        "id": postmortem.id,
        "spec_id": postmortem.spec_id,
        "agent": postmortem.agent,
        "failure_kind": postmortem.failure_kind,
        "summary": postmortem.summary,
        "findings_excerpt": list(postmortem.findings_excerpt),
        "recovery_attempted": postmortem.recovery_attempted,
        "outcome": postmortem.outcome,
        "recorded_at": postmortem.recorded_at.isoformat(),
        "tags": list(postmortem.tags),
    }


def parse_postmortem(payload: dict[str, Any]) -> Postmortem:
    missing = [f for f in _REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ValueError(f"parse_postmortem: missing required fields: {missing!r}")

    failure_kind = payload["failure_kind"]
    if failure_kind not in _VALID_FAILURE_KINDS:
        raise ValueError(
            f"parse_postmortem: invalid failure_kind {failure_kind!r}; "
            f"expected one of {sorted(_VALID_FAILURE_KINDS)}"
        )

    outcome = payload["outcome"]
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(
            f"parse_postmortem: invalid outcome {outcome!r}; "
            f"expected one of {sorted(_VALID_OUTCOMES)}"
        )

    recorded_at_raw = payload["recorded_at"]
    if isinstance(recorded_at_raw, datetime):
        recorded_at = recorded_at_raw
    else:
        recorded_at = datetime.fromisoformat(str(recorded_at_raw))
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)

    return Postmortem(
        id=str(payload["id"]),
        spec_id=str(payload["spec_id"]),
        agent=str(payload["agent"]),
        failure_kind=failure_kind,
        summary=str(payload["summary"]),
        findings_excerpt=tuple(str(f) for f in (payload["findings_excerpt"] or [])),
        recovery_attempted=str(payload["recovery_attempted"]),
        outcome=outcome,
        recorded_at=recorded_at,
        tags=tuple(str(t) for t in (payload["tags"] or [])),
    )


def new_postmortem_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "Postmortem",
    "PostmortemQuery",
    "new_postmortem_id",
    "parse_postmortem",
    "serialise_postmortem",
]
