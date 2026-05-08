from __future__ import annotations

import logging
import os
from collections.abc import Callable, Generator
from difflib import SequenceMatcher
from typing import Any, cast

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    critical_review_step_to_output_key,
    is_empty_review,
    is_quality_gate_enabled,
    load_enforcement_policy,
    max_planning_review_retries,
    min_review_content_chars,
    planning_review_resume_step,
    planning_review_target_step,
)
from backend.App.orchestration.application.enforcement.verification_contract import (
    is_human_gate_in_pipeline,
)
from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    record_planning_review_blocker,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import DefectReport, cluster_defects
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase
from backend.App.orchestration.application.enforcement.machine_transitions import (
    sync_pipeline_machine,
    transition_pipeline_phase,
)

_logger = logging.getLogger(__name__)


def enforce_planning_review_gate(
    state: PipelineState,
    *,
    step_id: str,
    review_output: str,
) -> None:
    from backend.App.orchestration.domain.quality_gate_policy import extract_verdict

    resume_step = planning_review_resume_step().get(step_id)
    if not resume_step:
        return
    verdict = extract_verdict(review_output or "")
    if verdict != "NEEDS_WORK":
        return

    pipeline_step_ids = cast(list[str], state.get("_pipeline_step_ids") or [])
    if resume_step not in pipeline_step_ids:
        _logger.warning(
            "Planning gate: %s returned NEEDS_WORK but human gate %r is NOT in the pipeline — continuing. "
            "Add %r to the pipeline if you want manual review.",
            step_id, resume_step, resume_step,
        )
        return

    raise HumanApprovalRequired(
        step=step_id,
        detail=(
            f"Planning gate: {step_id} returned NEEDS_WORK. "
            "Downstream planning/execution is blocked until explicit human override "
            "or a corrected planning artifact is provided."
        ),
        partial_state={
            critical_review_step_to_output_key().get(step_id, f"{step_id}_output"): review_output,
        },
        resume_pipeline_step=resume_step,
    )


def enter_fix_cycle_or_escalate(
    state: PipelineState,
    machine: PipelineMachine,
    report: DefectReport,
    *,
    step_id: str,
) -> None:
    transition_pipeline_phase(state, machine, PipelinePhase.FIX)
    if machine.should_stop_fix_cycle():
        if is_human_gate_in_pipeline(state, "human_dev"):
            raise HumanApprovalRequired(
                step=step_id,
                detail=(
                    f"Fix cycle budget exhausted after {machine.fix_cycles} iterations. "
                    "Human intervention is required."
                ),
                partial_state={
                    "open_defects": state.get("open_defects") or [],
                    "clustered_open_defects": state.get("clustered_open_defects") or [],
                },
                resume_pipeline_step="human_dev",
            )
        _logger.warning(
            "Fix cycle budget exhausted after %d iterations but human_dev not in pipeline — continuing",
            machine.fix_cycles,
        )
        return

    for defect_category in (category or "uncategorized" for category in cluster_defects(report.open_p0 + report.open_p1)):
        if machine.record_defect_attempt(defect_category):
            raise HumanApprovalRequired(
                step=step_id,
                detail=(
                    f"Defect category '{defect_category}' exceeded retry budget. "
                    "Human intervention is required."
                ),
                partial_state={
                    "open_defects": state.get("open_defects") or [],
                    "clustered_open_defects": state.get("clustered_open_defects") or [],
                },
                resume_pipeline_step="human_dev",
            )
    sync_pipeline_machine(state, machine)


def _stale_review_similarity_threshold() -> float:
    policy = load_enforcement_policy()
    return float(
        os.getenv(
            "SWARM_STALE_REVIEW_SIMILARITY_THRESHOLD",
            str(policy.get("default_stale_review_similarity_threshold", 0.85)),
        ).strip()
    )


def _reviews_are_too_similar(previous_review: str, new_review: str) -> bool:
    similarity = SequenceMatcher(None, previous_review, new_review).ratio()
    return similarity > _stale_review_similarity_threshold()


def run_planning_review_retry_loop(
    state: Any,
    machine: PipelineMachine,
    step_id: str,
    base_agent_config: dict[str, Any],
    resolve_step: Callable,
    run_step_with_stream_progress: Callable,
    emit_completed: Callable,
) -> Generator[dict[str, Any], None, None]:
    from backend.App.orchestration.application.pipeline.pipeline_state_helpers import get_step_retries
    from backend.App.orchestration.domain.quality_gate_policy import extract_verdict, should_retry

    review_output_key = critical_review_step_to_output_key().get(step_id, f"{step_id}_output")
    review_output = str(state.get(review_output_key) or "")
    record_planning_review_blocker(state, step_id=step_id, review_output=review_output)
    verdict = extract_verdict(review_output)
    auto_retry_enabled = is_quality_gate_enabled(state)
    target_step = planning_review_target_step()[step_id]
    retries = get_step_retries(state, target_step)
    allowed_retries = max_planning_review_retries()
    decision = should_retry(verdict, retries, allowed_retries) if auto_retry_enabled else "escalate"

    if verdict == "NEEDS_WORK" and is_empty_review(review_output):
        _logger.warning(
            "Planning gate: %s returned NEEDS_WORK with empty/short review (%d chars < %d threshold) — escalating. task_id=%s",
            step_id, len(review_output.strip()), min_review_content_chars(), (state.get("task_id") or "")[:36],
        )
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Planning gate: {step_id} returned NEEDS_WORK but review is "
                f"empty/too short ({len(review_output.strip())} chars). "
                "Escalating without retry — check reviewer prompt/model."
            ),
        }
        decision = "escalate"

    previous_review_text = review_output

    while verdict == "NEEDS_WORK" and decision == "retry":
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Planning gate: {step_id} returned NEEDS_WORK "
                f"(retry {retries + 1}/{allowed_retries}). Re-running {target_step} with reviewer feedback..."
            ),
        }
        step_retries_map = dict(state.get("step_retries") or {})
        step_retries_map[target_step] = retries + 1
        state["step_retries"] = step_retries_map

        if target_step and review_output:
            feedback = dict(state.get("planning_review_feedback") or {})
            feedback[target_step] = review_output
            state["planning_review_feedback"] = feedback
            _logger.info(
                "Planning gate: injected %d chars of reviewer feedback for %s retry",
                len(review_output), target_step,
            )

        _, target_func = resolve_step(target_step, base_agent_config)
        yield {"agent": target_step, "status": "in_progress", "message": f"{target_step} (planning retry)"}
        yield from run_step_with_stream_progress(target_step, target_func, state)
        yield emit_completed(target_step, state)

        _, review_func = resolve_step(step_id, base_agent_config)
        yield {"agent": step_id, "status": "in_progress", "message": f"{step_id} (planning retry)"}
        yield from run_step_with_stream_progress(step_id, review_func, state)
        yield emit_completed(step_id, state)

        review_output = str(state.get(review_output_key) or "")
        record_planning_review_blocker(state, step_id=step_id, review_output=review_output)
        verdict = extract_verdict(review_output)

        if verdict == "NEEDS_WORK" and previous_review_text and _reviews_are_too_similar(previous_review_text, review_output):
            _logger.warning(
                "Planning gate: %s reviewer produced near-identical review — auto-approving to break hallucination loop. task_id=%s",
                step_id, (state.get("task_id") or "")[:36],
            )
            verdict = "OK"

        if verdict == "NEEDS_WORK" and is_empty_review(review_output):
            _logger.warning(
                "Planning gate: %s retry produced empty/short review (%d chars) — escalating. task_id=%s",
                step_id, len(review_output.strip()), (state.get("task_id") or "")[:36],
            )
            yield {
                "agent": "orchestrator",
                "status": "progress",
                "message": (
                    f"Planning gate: {step_id} retry returned empty/short review "
                    f"({len(review_output.strip())} chars). Escalating."
                ),
            }
            break

        previous_review_text = review_output
        retries = get_step_retries(state, target_step)
        decision = should_retry(verdict, retries, allowed_retries)

    if verdict == "NEEDS_WORK":
        yield {
            "agent": "orchestrator",
            "status": "progress",
            "message": (
                f"Planning gate: {step_id} still NEEDS_WORK after {retries}/{allowed_retries} retries. "
                "Proceeding with current output. Consider human review."
            ),
        }
        from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
            record_ring_unresolved_escalation,
        )
        record_ring_unresolved_escalation(
            state, step_id=step_id, verdict=verdict,
            retries=retries, max_retries=allowed_retries,
            reason=f"planning review {step_id} exhausted retries with NEEDS_WORK",
        )

    enforce_planning_review_gate(state, step_id=step_id, review_output=review_output)
