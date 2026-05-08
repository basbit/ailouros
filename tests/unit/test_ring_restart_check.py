from backend.App.orchestration.application.pipeline.ring_restart_check import (
    build_ring_restart_defect_context,
    build_ring_restart_event,
    collect_failed_verification_gates,
    evaluate_ring_restart,
    ring_max_restarts_default,
    topology_from_agent_config,
)


def test_topology_from_agent_config_extracts_ring():
    cfg = {"swarm": {"topology": "ring"}}
    assert topology_from_agent_config(cfg) == "ring"


def test_topology_from_agent_config_returns_empty_when_missing():
    assert topology_from_agent_config({}) == ""
    assert topology_from_agent_config({"swarm": {}}) == ""
    assert topology_from_agent_config(None) == ""  # type: ignore[arg-type]


def test_ring_max_restarts_default_env_override(monkeypatch):
    monkeypatch.setenv("SWARM_RING_MAX_RESTARTS", "5")
    assert ring_max_restarts_default() == 5


def test_ring_max_restarts_default_fallback(monkeypatch):
    monkeypatch.delenv("SWARM_RING_MAX_RESTARTS", raising=False)
    assert ring_max_restarts_default() == 2


def test_collect_failed_verification_gates_filters_failures():
    state = {
        "verification_gates": [
            {"gate_name": "build_gate", "passed": True},
            {"gate_name": "stub_gate", "passed": False},
            {"gate_name": "diff_risk_gate", "passed": False},
            {"gate_name": "spec_gate", "passed": True},
        ]
    }
    failed = collect_failed_verification_gates(state)
    assert failed == ["stub_gate", "diff_risk_gate"]


def test_collect_failed_verification_gates_empty():
    assert collect_failed_verification_gates({}) == []
    assert collect_failed_verification_gates({"verification_gates": []}) == []


def test_evaluate_ring_restart_fires_when_failed_gates_present():
    state = {
        "verification_gates": [{"gate_name": "stub_gate", "passed": False}],
    }
    result = evaluate_ring_restart(state, "ring", ["pm", "dev"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is True
    assert result["failed_verification_gates"] == ["stub_gate"]


def test_evaluate_ring_restart_no_fire_when_no_issues():
    state = {"verification_gates": [{"gate_name": "build_gate", "passed": True}]}
    result = evaluate_ring_restart(state, "ring", ["pm", "dev"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is False


def test_evaluate_ring_restart_respects_topology():
    state = {"verification_gates": [{"gate_name": "stub_gate", "passed": False}]}
    result = evaluate_ring_restart(state, "linear", ["pm", "dev"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is False


def test_evaluate_ring_restart_respects_max_restarts():
    state = {"verification_gates": [{"gate_name": "stub_gate", "passed": False}]}
    result = evaluate_ring_restart(state, "ring", ["pm", "dev"], ring_pass=2, ring_max_restarts=2)
    assert result["should_restart"] is False


def test_evaluate_ring_restart_respects_pipeline_steps_required():
    state = {"verification_gates": [{"gate_name": "stub_gate", "passed": False}]}
    result = evaluate_ring_restart(state, "ring", None, ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is False


def test_evaluate_ring_restart_fires_on_open_defects():
    state = {"open_defects": [{"id": "D1", "severity": "P0"}]}
    result = evaluate_ring_restart(state, "ring", ["pm"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is True


def test_evaluate_ring_restart_fires_on_warnings():
    state = {"verification_gate_warnings": "stub_gate failed"}
    result = evaluate_ring_restart(state, "ring", ["pm"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is True


def test_evaluate_ring_restart_consumes_unresolved_escalations():
    state: dict = {}
    from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
        record_ring_unresolved_escalation,
    )
    record_ring_unresolved_escalation(
        state, step_id="review_devops", verdict="NEEDS_WORK",
        retries=2, max_retries=2, reason="stuck",
    )
    result = evaluate_ring_restart(state, "ring", ["pm"], ring_pass=0, ring_max_restarts=2)
    assert result["should_restart"] is True
    assert "_ring_unresolved_escalations" not in state


def test_build_ring_restart_defect_context_includes_failed_gates():
    evaluation = {
        "open_defects": [],
        "ring_unresolved": [],
        "failed_verification_gates": ["stub_gate", "diff_risk_gate"],
        "verification_warnings_text": "",
        "ring_pass": 0,
        "ring_max_restarts": 2,
    }
    context = build_ring_restart_defect_context(evaluation)
    assert "stub_gate" in context
    assert "diff_risk_gate" in context
    assert "Failed verification gates" in context


def test_build_ring_restart_event_payload():
    evaluation = {
        "open_defects": [{"id": "D1"}],
        "ring_unresolved": [{"step_id": "review_qa"}],
        "failed_verification_gates": ["stub_gate"],
        "verification_warnings_text": "warning text",
        "ring_pass": 0,
        "ring_max_restarts": 2,
    }
    event = build_ring_restart_event(evaluation)
    assert event["status"] == "ring_restart"
    assert event["restart_pass"] == 1
    assert event["defect_count"] == 4
    assert "Ring pass 1/2" in event["message"]
