"""Domain: pure routing logic for quality gates and verdict constants.

No infrastructure imports — stdlib only.
"""

from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# §9.3: Shared verdict/routing constants — single source of truth.
# Clarify step output prefixes (used by pm.py clarify_input_node).
CLARIFY_SIMPLE_ANSWER = "SIMPLE_ANSWER"
CLARIFY_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
CLARIFY_READY = "READY"

# Review verdicts (used by quality gate, review nodes).
VERDICT_OK = "OK"
VERDICT_NEEDS_WORK = "NEEDS_WORK"
VERDICT_APPROVED = "APPROVED"

# Process-level telemetry counters.
_invalid_response_shape_count: int = 0


def get_quality_gate_metrics() -> dict[str, int]:
    """Return process-level quality-gate telemetry counters.

    ``invalid_response_shape`` counts how many times ``extract_verdict`` was
    called on text that contained no VERDICT marker — indicating the reviewer
    bypassed the upstream format gate (or the gate itself failed).
    """
    return {"invalid_response_shape": _invalid_response_shape_count}


def extract_verdict(text: str) -> str:
    """Extract APPROVED/NEEDS_WORK/ESCALATE from reviewer output.

    Parses a ``VERDICT: <word>`` marker from *text*.
    Returns the uppercase verdict word, or ``'OK'`` when no marker is found.

    When no marker is found, logs a warning so operators can detect cases where
    the reviewer did not produce a valid verdict (invalid_response_shape).
    The upstream VERDICT gate in ``review_moa.run_reviewer_or_moa`` should have
    already inserted a ``VERDICT: NEEDS_WORK`` before this point.
    """
    global _invalid_response_shape_count
    m = re.search(r"VERDICT\s*:\s*(\w+)", text or "", re.IGNORECASE)
    if not m:
        _invalid_response_shape_count += 1
        _log.warning(
            "extract_verdict: no VERDICT marker found (invalid_response_shape=%d, len=%d). "
            "Defaulting to OK. Check reviewer prompt and SWARM_REVIEWER_MAX_OUTPUT_TOKENS.",
            _invalid_response_shape_count,
            len(text or ""),
        )
    return m.group(1).upper() if m else "OK"


def should_retry(verdict: str, retries: int, max_retries: int) -> str:
    """Decide whether to retry, continue, or escalate based on the verdict.

    Args:
        verdict: uppercase verdict string (e.g. ``'NEEDS_WORK'``, ``'APPROVED'``).
        retries: number of retries already consumed for this step.
        max_retries: configured maximum number of retries.

    Returns:
        ``'retry'`` when the verdict is NEEDS_WORK and retries are available,
        ``'escalate'`` when retries are exhausted,
        ``'continue'`` for any other verdict.
    """
    if verdict != VERDICT_NEEDS_WORK:
        return "continue"
    if retries < max_retries:
        return "retry"
    return "escalate"
