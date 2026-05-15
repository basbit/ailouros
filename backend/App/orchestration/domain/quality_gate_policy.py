
from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

CLARIFY_SIMPLE_ANSWER = "SIMPLE_ANSWER"
CLARIFY_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
CLARIFY_READY = "READY"

VERDICT_OK = "OK"
VERDICT_NEEDS_WORK = "NEEDS_WORK"
VERDICT_APPROVED = "APPROVED"

_invalid_response_shape_count: int = 0

VERDICT_RE = re.compile(
    r"VERDICT\b[\s*_`]*:[\s*_`]*([A-Z_][A-Z0-9_]*)",
    re.IGNORECASE,
)

_NEEDS_WORK_RE = re.compile(
    r"VERDICT\b[\s*_`]*:[\s*_`]*NEEDS_WORK\b",
    re.IGNORECASE,
)


def get_quality_gate_metrics() -> dict[str, int]:
    return {"invalid_response_shape": _invalid_response_shape_count}


def extract_verdict(text: str) -> str:
    global _invalid_response_shape_count
    matches = list(VERDICT_RE.finditer(text or ""))
    if not matches:
        _invalid_response_shape_count += 1
        _log.warning(
            "extract_verdict: no VERDICT marker found (invalid_response_shape=%d, len=%d). "
            "Defaulting to NEEDS_WORK. Check reviewer prompt and SWARM_REVIEWER_MAX_OUTPUT_TOKENS.",
            _invalid_response_shape_count,
            len(text or ""),
        )
        return VERDICT_NEEDS_WORK
    return matches[-1].group(1).upper()


def _sentinel_structured_defect() -> dict[str, str]:
    return {
        "id": "D0",
        "severity": "P0",
        "description": "reviewer did not provide structured blocking defect report",
        "remediation": "re-run review with defect_report requirement",
    }


def extract_defect_report(text: str) -> list[dict[str, Any]]:
    if not _NEEDS_WORK_RE.search(text or ""):
        return []

    from backend.App.orchestration.domain.defect import parse_defect_report

    report = parse_defect_report(text or "")
    if report.has_blockers:
        return [defect.to_dict() for defect in report.defects]

    if not report.defects:
        _log.warning(
            "extract_defect_report: NEEDS_WORK verdict present but no <defect_report> block found. "
            "Returning sentinel defect."
        )
        return [_sentinel_structured_defect()]

    _log.warning(
        "extract_defect_report: NEEDS_WORK verdict present but defect_report has no "
        "open P0/P1 blocker. Returning sentinel defect."
    )
    return [_sentinel_structured_defect()]


def should_retry(verdict: str, retries: int, max_retries: int) -> str:
    if verdict != VERDICT_NEEDS_WORK:
        return "continue"
    if retries < max_retries:
        return "retry"
    return "escalate"
