from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, cast

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    load_enforcement_policy,
    repair_contract_steps,
)
from backend.App.orchestration.application.enforcement.planning_review_enforcer import (
    enter_fix_cycle_or_escalate,
)
from backend.App.orchestration.application.enforcement.repair_contract import (
    build_repair_contract,
    evaluate_retry_progress,
    retry_should_block,
)
from backend.App.orchestration.application.enforcement.verification_contract import (
    is_human_gate_in_pipeline,
    require_structured_blockers,
    verification_layer_status_message,
)
from backend.App.orchestration.application.enforcement.dev_verification_gate import (
    run_post_dev_verification_gates,
)
from backend.App.orchestration.application.enforcement.machine_transitions import (
    transition_pipeline_phase,
)
from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    load_defect_report,
    record_open_defects,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase

_logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _repair_route_steps() -> tuple[str, str]:
    steps = repair_contract_steps()
    human_step_id = steps.get("human_step_id")
    resume_pipeline_step = steps.get("resume_pipeline_step")
    if not human_step_id or not resume_pipeline_step:
        raise RuntimeError("pipeline_enforcement_policy.repair_contract is incomplete")
    return human_step_id, resume_pipeline_step


def _state_data(state: PipelineState) -> dict[str, Any]:
    return cast(dict[str, Any], state)


def _written_paths(state: PipelineState) -> list[str]:
    workspace_writes = state.get("workspace_writes") or {}
    if not isinstance(workspace_writes, dict):
        return []
    return sorted(
        {
            *(workspace_writes.get("written") or []),
            *(workspace_writes.get("patched") or []),
            *(workspace_writes.get("udiff_applied") or []),
        }
    )


def run_dev_review_quality_gate(
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
    from backend.App.orchestration.application.pipeline.pipeline_state_helpers import (
        get_step_retries,
    )
    from backend.App.orchestration.domain.quality_gate_policy import (
        extract_verdict,
        should_retry,
    )

    if not _quality_gate_enabled(state):
        return

    verdict = extract_verdict(state.get("dev_review_output") or "")
    report = load_defect_report(state, "dev_defect_report")
    record_open_defects(state, report)
    require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
    dev_retries = get_step_retries(state, "dev")
    max_retries = _max_step_retries_env()
    decision = should_retry(verdict, dev_retries, max_retries)

    previous_dev_review_text = str(state.get("dev_review_output") or "")

    while decision == "retry":
        enter_fix_cycle_or_escalate(state, machine, report, step_id="review_dev")
        contract = build_repair_contract(previous_dev_review_text)
        if not contract.is_empty():
            _state_data(state)["repair_contract"] = contract.to_dict()
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Quality gate: review_dev returned NEEDS_WORK "
                f"(retry {dev_retries + 1}/{max_retries}). Re-running dev..."
            ),
        }
        step_retries_map = dict(state.get("step_retries") or {})
        step_retries_map["dev"] = dev_retries + 1
        state["step_retries"] = step_retries_map

        _, dev_func = resolve_step("dev", base_agent_config)
        yield {"agent": "dev", "status": "in_progress", "message": "Dev (retry)"}
        yield from run_step_with_stream_progress("dev", dev_func, state)
        yield emit_completed("dev", state)

        gate_results = run_post_dev_verification_gates(state)
        transition_pipeline_phase(
            state,
            machine,
            PipelinePhase.VERIFY,
            source="verification_layer",
        )
        yield {
            "agent": "verification_layer",
            "status": "completed",
            "message": verification_layer_status_message(
                gate_results,
                context="after dev retry",
            ),
        }

        if not contract.is_empty():
            human_step_id, resume_pipeline_step = _repair_route_steps()
            written_paths = _written_paths(state)
            block_reason = retry_should_block(contract, written_paths)
            if block_reason:
                _logger.error(
                    "Repair contract blocked retry: %s task_id=%s",
                    block_reason, (state.get("task_id") or "")[:36],
                )
                _state_data(state)["_repair_contract_block_reason"] = block_reason
                if is_human_gate_in_pipeline(state, human_step_id):
                    raise HumanApprovalRequired(
                        step="repair_contract",
                        detail=block_reason,
                        partial_state={
                            "repair_contract": contract.to_dict(),
                            "files_written": written_paths,
                        },
                        resume_pipeline_step=resume_pipeline_step,
                    )
                raise RuntimeError(block_reason)

        _, review_func = resolve_step("review_dev", base_agent_config)
        yield {"agent": "review_dev", "status": "in_progress", "message": "Review dev (retry)"}
        yield from run_step_with_stream_progress("review_dev", review_func, state)
        yield emit_completed("review_dev", state)

        dev_retries = get_step_retries(state, "dev")
        new_dev_review = str(state.get("dev_review_output") or "")
        verdict = extract_verdict(new_dev_review)

        if not contract.is_empty():
            written_paths = _written_paths(state)
            progress = evaluate_retry_progress(contract, written_paths, new_dev_review)
            _state_data(state)["repair_contract_progress"] = progress.to_dict()
            if progress.fixed and not progress.worse:
                _logger.info(
                    "Repair contract: dev retry fixed %d/%d defects (worse=0). task_id=%s",
                    len(progress.fixed), len(contract.defects),
                    (state.get("task_id") or "")[:36],
                )

        if verdict == "NEEDS_WORK" and previous_dev_review_text:
            policy = load_enforcement_policy()
            stale_threshold = float(
                os.getenv(
                    "SWARM_STALE_REVIEW_SIMILARITY_THRESHOLD",
                    str(policy.get("default_stale_review_similarity_threshold", 0.85)),
                ).strip()
            )
            similarity = SequenceMatcher(
                None,
                previous_dev_review_text,
                new_dev_review,
            ).ratio()
            if similarity > stale_threshold:
                _logger.warning(
                    "Quality gate: review_dev produced near-identical review — auto-approving. task_id=%s",
                    (state.get("task_id") or "")[:36],
                )
                verdict = "OK"
        previous_dev_review_text = new_dev_review

        report = load_defect_report(state, "dev_defect_report")
        record_open_defects(state, report)
        require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
        decision = should_retry(verdict, dev_retries, max_retries)

    if verdict == "NEEDS_WORK" and decision != "escalate":
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Quality gate: review_dev still NEEDS_WORK after {max_retries} retries. "
                "Proceeding to QA. Consider human review."
            ),
        }
        from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
            record_ring_unresolved_escalation,
        )
        record_ring_unresolved_escalation(
            state, step_id="review_dev", verdict=verdict,
            retries=dev_retries, max_retries=max_retries,
            reason="review_dev exhausted retries with NEEDS_WORK — proceeding to QA",
        )

    if decision == "escalate":
        from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
            record_ring_unresolved_escalation,
        )
        record_ring_unresolved_escalation(
            state, step_id="review_dev", verdict=verdict,
            retries=dev_retries, max_retries=max_retries,
            reason="review_dev decision=escalate",
        )
        if is_human_gate_in_pipeline(state, "human_dev"):
            raise HumanApprovalRequired(
                step="review_dev",
                detail=(
                    f"Quality gate: dev retries exhausted ({dev_retries}/{max_retries}). "
                    "Structured defects require manual intervention."
                ),
                partial_state={"open_defects": state.get("open_defects") or []},
                resume_pipeline_step="human_dev",
            )
        _logger.warning(
            "Quality gate: dev retries exhausted (%d/%d) but human_dev not in pipeline — continuing",
            dev_retries, max_retries,
        )
