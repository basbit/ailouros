from __future__ import annotations

import json

from backend.App.orchestration.domain.quality_gate_policy import (
    extract_defect_report,
    extract_verdict,
)


def test_extract_verdict_uses_last_valid_marker() -> None:
    text = "VERDICT: NEEDS_WORK\nAfter retry this is fixed.\nVERDICT: OK"
    assert extract_verdict(text) == "OK"


def test_extract_verdict_missing_marker_is_needs_work() -> None:
    assert extract_verdict("review prose without a verdict") == "NEEDS_WORK"


def test_extract_defect_report_accepts_json_defect_report() -> None:
    payload = {
        "defects": [
            {
                "id": "D1",
                "title": "Broken launch command",
                "severity": "P1",
                "file_paths": ["run_manifest.json"],
                "fixed": False,
            }
        ]
    }
    text = f"<defect_report>{json.dumps(payload)}</defect_report>\nVERDICT: NEEDS_WORK"

    defects = extract_defect_report(text)

    assert defects[0]["id"] == "D1"
    assert defects[0]["severity"] == "P1"


def test_extract_defect_report_sentinel_for_needs_work_without_blocker() -> None:
    text = '<defect_report>{"defects":[]}</defect_report>\nVERDICT: NEEDS_WORK'

    defects = extract_defect_report(text)

    assert defects[0]["id"] == "D0"
    assert defects[0]["severity"] == "P0"


def test_extract_defect_report_rejects_fenced_block() -> None:
    text = (
        "```json\n"
        '<defect_report>{"defects":[{"id":"D1","severity":"P1"}]}</defect_report>\n'
        "```\n"
        "VERDICT: NEEDS_WORK"
    )

    defects = extract_defect_report(text)

    assert defects[0]["id"] == "D0"
