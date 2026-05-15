from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class CodegenFeedback:
    id: str
    spec_id: str
    agent: str
    target_file: str
    verdict: Literal["accept", "reject", "edit"]
    user_edit_diff: Optional[str]
    reason: Optional[str]
    recorded_at: datetime
    tags: tuple[str, ...]


_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "spec_id",
    "agent",
    "target_file",
    "verdict",
    "recorded_at",
)

_VALID_VERDICTS: frozenset[str] = frozenset({"accept", "reject", "edit"})


def serialise_feedback(feedback: CodegenFeedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "spec_id": feedback.spec_id,
        "agent": feedback.agent,
        "target_file": feedback.target_file,
        "verdict": feedback.verdict,
        "user_edit_diff": feedback.user_edit_diff,
        "reason": feedback.reason,
        "recorded_at": feedback.recorded_at.isoformat(),
        "tags": list(feedback.tags),
    }


def parse_feedback(payload: dict[str, Any]) -> CodegenFeedback:
    missing = [f for f in _REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ValueError(f"parse_feedback: missing required fields: {missing!r}")

    verdict = payload["verdict"]
    if verdict not in _VALID_VERDICTS:
        raise ValueError(
            f"parse_feedback: invalid verdict {verdict!r}; "
            f"expected one of {sorted(_VALID_VERDICTS)}"
        )

    recorded_at_raw = payload["recorded_at"]
    if isinstance(recorded_at_raw, datetime):
        recorded_at = recorded_at_raw
    else:
        recorded_at = datetime.fromisoformat(str(recorded_at_raw))
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)

    return CodegenFeedback(
        id=str(payload["id"]),
        spec_id=str(payload["spec_id"]),
        agent=str(payload["agent"]),
        target_file=str(payload["target_file"]),
        verdict=verdict,
        user_edit_diff=str(payload["user_edit_diff"]) if payload.get("user_edit_diff") is not None else None,
        reason=str(payload["reason"]) if payload.get("reason") is not None else None,
        recorded_at=recorded_at,
        tags=tuple(str(t) for t in (payload.get("tags") or [])),
    )


def new_feedback_id() -> str:
    return str(uuid.uuid4())


__all__ = [
    "CodegenFeedback",
    "new_feedback_id",
    "parse_feedback",
    "serialise_feedback",
]
