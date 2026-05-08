from __future__ import annotations

from typing import Any

from backend.App.orchestration.application.pipeline.ephemeral_state import (
    append_ephemeral,
    get_ephemeral,
    pop_ephemeral,
)

_RING_UNRESOLVED_STATE_KEY = "_ring_unresolved_escalations"


def record_ring_unresolved_escalation(
    state: Any,
    *,
    step_id: str,
    verdict: str,
    retries: int,
    max_retries: int,
    reason: str = "",
) -> None:
    escalation_entry: dict[str, Any] = {
        "step_id": step_id,
        "verdict": verdict,
        "retries": retries,
        "max_retries": max_retries,
        "reason": reason,
    }
    append_ephemeral(state, _RING_UNRESOLVED_STATE_KEY, escalation_entry)


def consume_ring_unresolved_escalations(state: Any) -> list[dict[str, Any]]:
    existing = get_ephemeral(state, _RING_UNRESOLVED_STATE_KEY, default=None)
    if not isinstance(existing, list) or not existing:
        return []
    pop_ephemeral(state, _RING_UNRESOLVED_STATE_KEY)
    return list(existing)


def ring_unresolved_escalations(state: Any) -> list[dict[str, Any]]:
    existing = get_ephemeral(state, _RING_UNRESOLVED_STATE_KEY, default=None)
    if not isinstance(existing, list):
        return []
    return list(existing)
