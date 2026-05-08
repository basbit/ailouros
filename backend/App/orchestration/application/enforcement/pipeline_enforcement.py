from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from typing import Any

from backend.App.orchestration.application.enforcement.dev_patch_errors_enforcer import (
    enforce_dev_patch_errors,
)
from backend.App.orchestration.application.enforcement.dev_review_gate import run_dev_review_quality_gate
from backend.App.orchestration.application.enforcement.dev_verification_gate import run_post_dev_verification_gates
from backend.App.orchestration.application.enforcement.enforcement_policy import (
    critical_review_step_to_output_key,
    dev_verification_step_id,
    devops_script_contract_step_ids,
    is_empty_review as _is_empty_review,
    max_planning_review_retries as _max_planning_review_retries,
    min_review_content_chars as _min_review_content_chars_fn,
    planning_review_target_step,
    planning_review_resume_step as _planning_review_resume_step,
    required_non_empty_output_steps,
)
from backend.App.orchestration.application.enforcement.machine_transitions import (
    finalize_pipeline_machine,
    prepare_pipeline_machine_for_step,
    sync_pipeline_machine,
    transition_pipeline_phase,
)
from backend.App.orchestration.application.enforcement.planning_review_enforcer import (
    enforce_planning_review_gate,
    enter_fix_cycle_or_escalate,
    run_planning_review_retry_loop,
)
from backend.App.orchestration.application.enforcement.devops_script_contract import (
    enforce_devops_script_contract,
)
from backend.App.orchestration.application.enforcement.pre_review_blockers import (
    enforce_pre_review_blockers,
)
from backend.App.orchestration.application.enforcement.review_qa_gate import run_qa_review_quality_gate
from backend.App.orchestration.application.enforcement.swarm_file_enforcer import enforce_swarm_file_tags
from backend.App.orchestration.application.enforcement.verification_contract import (
    expected_trusted_verification_commands,
    is_human_gate_in_pipeline,
    normalize_trusted_verification_commands,
    require_structured_blockers,
    verification_layer_status_message,
)
from backend.App.orchestration.application.pipeline.pipeline_state import ARTIFACT_AGENT_OUTPUT_KEYS
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase

_logger = logging.getLogger(__name__)

_CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY: dict[str, str] = critical_review_step_to_output_key()
_MIN_REVIEW_CONTENT_CHARS: int = _min_review_content_chars_fn()
_ARTIFACT_OUTPUT_KEY_BY_STEP: dict[str, str] = dict(ARTIFACT_AGENT_OUTPUT_KEYS)
_DEV_VERIFICATION_STEP_ID: str = dev_verification_step_id()
_DEVOPS_SCRIPT_CONTRACT_STEP_IDS: frozenset[str] = devops_script_contract_step_ids()

_should_block_for_human = is_human_gate_in_pipeline


def run_post_step_enforcement(
    state: Any,
    machine: PipelineMachine,
    step_id: str,
    base_agent_config: dict[str, Any],
    resolve_step: Callable[..., Any],
    run_step_with_stream_progress: Callable[..., Any],
    emit_completed: Callable[..., dict[str, Any]],
) -> Generator[dict[str, Any], None, None]:
    enforce_non_empty_step_output(state, step_id)

    if step_id in planning_review_target_step():
        yield from run_planning_review_retry_loop(
            state, machine, step_id, base_agent_config,
            resolve_step, run_step_with_stream_progress, emit_completed,
        )

    if step_id == _DEV_VERIFICATION_STEP_ID:
        yield from enforce_swarm_file_tags(
            state,
            resolve_step=resolve_step,
            base_agent_config=base_agent_config,
            run_step_with_stream_progress=run_step_with_stream_progress,
            emit_completed=emit_completed,
        )
        gate_results = run_post_dev_verification_gates(state)
        while state.get("_dev_patch_errors_for_retry"):
            previous_retry_count = int(state.get("_dev_patch_retry_count") or 0)
            yield from enforce_dev_patch_errors(
                state,
                resolve_step=resolve_step,
                base_agent_config=base_agent_config,
                run_step_with_stream_progress=run_step_with_stream_progress,
                emit_completed=emit_completed,
            )
            if int(state.get("_dev_patch_retry_count") or 0) == previous_retry_count:
                break
            gate_results = run_post_dev_verification_gates(state)
        transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
        if gate_results:
            yield {
                "agent": "verification_layer",
                "status": "completed",
                "message": verification_layer_status_message(gate_results),
            }
        enforce_pre_review_blockers(state)

    if step_id in _DEVOPS_SCRIPT_CONTRACT_STEP_IDS:
        enforce_devops_script_contract(state)
        if state.get("_failed_trusted_gates"):
            enforce_pre_review_blockers(state)

    if step_id == "review_dev":
        yield from run_dev_review_quality_gate(
            state, machine, base_agent_config,
            resolve_step, run_step_with_stream_progress, emit_completed,
        )

    if step_id == "review_qa":
        yield from run_qa_review_quality_gate(
            state, machine, base_agent_config,
            resolve_step, run_step_with_stream_progress, emit_completed,
        )


def enforce_non_empty_step_output(state: Any, step_id: str) -> None:
    if step_id not in required_non_empty_output_steps():
        return
    output_key = _ARTIFACT_OUTPUT_KEY_BY_STEP.get(step_id)
    if not output_key:
        raise RuntimeError(f"{step_id}: required output key is not declared in the pipeline catalog")
    output_value = str(state.get(output_key) or "").strip()
    if output_value:
        return
    raise RuntimeError(f"{step_id}: required output {output_key} is empty")


__all__ = (
    "_CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY",
    "_MIN_REVIEW_CONTENT_CHARS",
    "_is_empty_review",
    "_max_planning_review_retries",
    "_planning_review_resume_step",
    "_should_block_for_human",
    "enforce_planning_review_gate",
    "enter_fix_cycle_or_escalate",
    "enforce_non_empty_step_output",
    "expected_trusted_verification_commands",
    "finalize_pipeline_machine",
    "normalize_trusted_verification_commands",
    "prepare_pipeline_machine_for_step",
    "require_structured_blockers",
    "run_post_dev_verification_gates",
    "run_post_step_enforcement",
    "sync_pipeline_machine",
    "transition_pipeline_phase",
    "verification_layer_status_message",
)
