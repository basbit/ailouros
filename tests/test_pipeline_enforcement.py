from __future__ import annotations

import pytest

from backend.App.orchestration.application.pipeline_enforcement import (
    enforce_planning_review_gate,
    verification_layer_status_message,
)
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired


def test_enforce_planning_review_gate_blocks_review_dev_lead():
    state = {"_pipeline_step_ids": ["review_dev_lead", "human_dev_lead"]}
    with pytest.raises(HumanApprovalRequired) as exc_info:
        enforce_planning_review_gate(
            state,
            step_id="review_dev_lead",
            review_output="VERDICT: NEEDS_WORK\nMissing deliverables contract.",
        )

    assert exc_info.value.resume_pipeline_step == "human_dev_lead"
    assert exc_info.value.partial_state == {
        "dev_lead_review_output": "VERDICT: NEEDS_WORK\nMissing deliverables contract.",
    }


def test_verification_layer_status_message_reports_failures():
    message = verification_layer_status_message(
        [
            {"gate_name": "build_gate", "passed": True},
            {"gate_name": "spec_gate", "passed": False},
            {"gate_name": "stub_gate", "passed": False},
        ],
        context="after dev retry",
    )

    assert message == "Trusted verification gates found issues after dev retry: spec_gate, stub_gate"


def test_verification_layer_status_message_reports_success():
    message = verification_layer_status_message(
        [
            {"gate_name": "build_gate", "passed": True},
            {"gate_name": "spec_gate", "passed": True},
        ]
    )

    assert message == "Trusted verification gates passed: build_gate, spec_gate"
