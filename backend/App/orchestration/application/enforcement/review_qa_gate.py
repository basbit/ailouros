from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from typing import Any

from backend.App.orchestration.application.enforcement.dev_verification_gate import (
    run_post_dev_verification_gates,
)
from backend.App.orchestration.application.enforcement.machine_transitions import transition_pipeline_phase
from backend.App.orchestration.application.enforcement.planning_review_enforcer import enter_fix_cycle_or_escalate
from backend.App.orchestration.application.enforcement.verification_contract import (
    is_human_gate_in_pipeline,
    require_structured_blockers,
    verification_layer_status_message,
)
from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    load_defect_report,
    merge_defect_reports,
    record_open_defects,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase

_logger = logging.getLogger(__name__)


def run_qa_review_quality_gate(
    state: PipelineState,
    machine: PipelineMachine,
    base_agent_config: dict[str, Any],
    resolve_step: Callable,
    run_step_with_stream_progress: Callable,
    emit_completed: Callable,
) -> Generator[dict[str, Any], None, None]:
    from backend.App.orchestration.application.routing.graph_builder import (
        _max_step_retries_env,
        _quality_gate_enabled,
    )
    from backend.App.orchestration.application.pipeline.pipeline_state_helpers import get_step_retries
    from backend.App.orchestration.domain.quality_gate_policy import extract_verdict, should_retry

    if not _quality_gate_enabled(state):
        return

    verdict = extract_verdict(state.get("qa_review_output") or "")
    report = merge_defect_reports(
        load_defect_report(state, "qa_defect_report"),
        load_defect_report(state, "qa_review_defect_report"),
    )
    record_open_defects(state, report)
    require_structured_blockers(report=report, verdict=verdict, step_id="review_qa")
    qa_retries = get_step_retries(state, "qa")
    max_retries = _max_step_retries_env()
    decision = should_retry(verdict, qa_retries, max_retries)

    while decision == "retry":
        enter_fix_cycle_or_escalate(state, machine, report, step_id="review_qa")
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Quality gate: review_qa returned NEEDS_WORK "
                f"(retry {qa_retries + 1}/{max_retries}). Re-running dev..."
            ),
        }
        step_retries_map = dict(state.get("step_retries") or {})
        step_retries_map["qa"] = qa_retries + 1
        state["step_retries"] = step_retries_map

        _, dev_func = resolve_step("dev", base_agent_config)
        yield {"agent": "dev", "status": "in_progress", "message": "Dev (retry from QA)"}
        yield from run_step_with_stream_progress("dev", dev_func, state)
        yield emit_completed("dev", state)

        gate_results = run_post_dev_verification_gates(state)
        transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
        yield {
            "agent": "verification_layer",
            "status": "completed",
            "message": verification_layer_status_message(gate_results, context="after QA-triggered dev retry"),
        }

        transition_pipeline_phase(state, machine, PipelinePhase.QA, source="system")
        _, qa_func = resolve_step("qa", base_agent_config)
        yield {"agent": "qa", "status": "in_progress", "message": "QA (retry)"}
        yield from run_step_with_stream_progress("qa", qa_func, state)
        yield emit_completed("qa", state)

        _, review_func = resolve_step("review_qa", base_agent_config)
        yield {"agent": "review_qa", "status": "in_progress", "message": "Review QA (retry)"}
        yield from run_step_with_stream_progress("review_qa", review_func, state)
        yield emit_completed("review_qa", state)

        qa_retries = get_step_retries(state, "qa")
        verdict = extract_verdict(state.get("qa_review_output") or "")
        report = merge_defect_reports(
            load_defect_report(state, "qa_defect_report"),
            load_defect_report(state, "qa_review_defect_report"),
        )
        record_open_defects(state, report)
        require_structured_blockers(report=report, verdict=verdict, step_id="review_qa")
        decision = should_retry(verdict, qa_retries, max_retries)

    if decision == "escalate":
        from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
            record_ring_unresolved_escalation,
        )
        record_ring_unresolved_escalation(
            state, step_id="review_qa", verdict=verdict,
            retries=qa_retries, max_retries=max_retries,
            reason="review_qa decision=escalate",
        )
        if is_human_gate_in_pipeline(state, "human_qa"):
            raise HumanApprovalRequired(
                step="review_qa",
                detail=(
                    f"Quality gate: QA retries exhausted ({qa_retries}/{max_retries}). "
                    "Structured defects require manual intervention."
                ),
                partial_state={"open_defects": state.get("open_defects") or []},
                resume_pipeline_step="human_qa",
            )
        _logger.warning(
            "Quality gate: QA retries exhausted (%d/%d) but human_qa not in pipeline — continuing",
            qa_retries, max_retries,
        )
