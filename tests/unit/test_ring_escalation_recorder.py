from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
    consume_ring_unresolved_escalations,
    record_ring_unresolved_escalation,
    ring_unresolved_escalations,
)


def test_record_and_consume_roundtrip():
    state: dict = {}
    record_ring_unresolved_escalation(
        state, step_id="review_devops", verdict="NEEDS_WORK",
        retries=2, max_retries=2, reason="exhausted",
    )
    record_ring_unresolved_escalation(
        state, step_id="review_qa", verdict="NEEDS_WORK",
        retries=2, max_retries=2, reason="escalate",
    )
    entries = ring_unresolved_escalations(state)
    assert len(entries) == 2
    assert entries[0]["step_id"] == "review_devops"
    assert entries[1]["step_id"] == "review_qa"

    consumed = consume_ring_unresolved_escalations(state)
    assert len(consumed) == 2
    assert ring_unresolved_escalations(state) == []


def test_consume_empty_state_returns_empty_list():
    state: dict = {}
    assert consume_ring_unresolved_escalations(state) == []
    assert ring_unresolved_escalations(state) == []


def test_entries_preserve_all_fields():
    state: dict = {}
    record_ring_unresolved_escalation(
        state, step_id="review_dev", verdict="NEEDS_WORK",
        retries=3, max_retries=5, reason="contract_missing",
    )
    entries = ring_unresolved_escalations(state)
    assert entries[0] == {
        "step_id": "review_dev",
        "verdict": "NEEDS_WORK",
        "retries": 3,
        "max_retries": 5,
        "reason": "contract_missing",
    }


def test_consume_clears_state_key():
    state: dict = {}
    record_ring_unresolved_escalation(
        state, step_id="review_dev", verdict="NEEDS_WORK",
        retries=1, max_retries=1,
    )
    assert "_ring_unresolved_escalations" in state
    consume_ring_unresolved_escalations(state)
    assert "_ring_unresolved_escalations" not in state
