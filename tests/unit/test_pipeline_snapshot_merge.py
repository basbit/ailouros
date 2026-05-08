from backend.App.orchestration.application.streaming.pipeline_sse_handler import (
    _merge_runtime_state_into_snapshot,
)


def test_merge_copies_state_keys_to_snapshot():
    pipeline_snapshot: dict = {"pm_output": "planning", "agent_config": {}}
    final_pipeline_state = {
        "verification_gates": [{"gate_name": "stub_gate", "passed": False}],
        "open_defects": [{"id": "D1"}],
        "pipeline_metrics": {"step_metrics": {"steps": []}},
        "verification_gate_warnings": "stub_gate failed",
    }
    _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)
    assert pipeline_snapshot["verification_gates"] == [{"gate_name": "stub_gate", "passed": False}]
    assert pipeline_snapshot["open_defects"] == [{"id": "D1"}]
    assert pipeline_snapshot["pipeline_metrics"] == {"step_metrics": {"steps": []}}
    assert pipeline_snapshot["verification_gate_warnings"] == "stub_gate failed"


def test_merge_preserves_existing_non_empty_snapshot_values():
    pipeline_snapshot: dict = {
        "verification_gates": [{"gate_name": "build_gate", "passed": True}],
        "open_defects": [{"id": "X"}],
    }
    final_pipeline_state = {
        "verification_gates": [{"gate_name": "stub_gate", "passed": False}],
        "open_defects": [],
    }
    _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)
    assert pipeline_snapshot["verification_gates"] == [{"gate_name": "build_gate", "passed": True}]
    assert pipeline_snapshot["open_defects"] == [{"id": "X"}]


def test_merge_empty_state_is_noop():
    pipeline_snapshot: dict = {"pm_output": "x"}
    _merge_runtime_state_into_snapshot(pipeline_snapshot, {})
    assert pipeline_snapshot == {"pm_output": "x"}


def test_merge_skips_keys_not_in_runtime_state():
    pipeline_snapshot: dict = {}
    final_pipeline_state = {"some_random_key": "value"}
    _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)
    assert "some_random_key" not in pipeline_snapshot


def test_merge_overwrites_empty_snapshot_collection():
    pipeline_snapshot: dict = {
        "verification_gates": [],
        "verification_gate_warnings": "",
    }
    final_pipeline_state = {
        "verification_gates": [{"gate_name": "stub_gate", "passed": False}],
        "verification_gate_warnings": "something failed",
    }
    _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)
    assert pipeline_snapshot["verification_gates"] == [{"gate_name": "stub_gate", "passed": False}]
    assert pipeline_snapshot["verification_gate_warnings"] == "something failed"


def test_merge_skips_none_values():
    pipeline_snapshot: dict = {}
    final_pipeline_state = {"verification_gates": None, "open_defects": None}
    _merge_runtime_state_into_snapshot(pipeline_snapshot, final_pipeline_state)
    assert "verification_gates" not in pipeline_snapshot
    assert "open_defects" not in pipeline_snapshot


def test_merge_handles_non_dict_input():
    pipeline_snapshot: dict = {"pm_output": "x"}
    _merge_runtime_state_into_snapshot(pipeline_snapshot, None)  # type: ignore[arg-type]
    assert pipeline_snapshot == {"pm_output": "x"}
