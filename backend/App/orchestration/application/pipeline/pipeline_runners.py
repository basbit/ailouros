from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from typing import Any, Optional, cast

from backend.App.orchestration.application.enforcement.machine_transitions import (
    finalize_pipeline_machine,
    prepare_pipeline_machine_for_step,
    sync_pipeline_machine,
)
from backend.App.orchestration.application.enforcement.pipeline_enforcement import (
    run_post_step_enforcement as _run_post_step_enforcement,
)
from backend.App.orchestration.application.enforcement.workspace_preflight import (
    enforce_workspace_preflight,
)
from backend.App.orchestration.application.pipeline.ephemeral_state import set_ephemeral
from backend.App.orchestration.application.pipeline.graph_runner import run_pipeline_stream_via_graph
from backend.App.orchestration.application.pipeline.pipeline_display import pipeline_step_in_progress_message
from backend.App.orchestration.application.pipeline.pipeline_runtime_support import record_open_defects as _record_open_defects
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.pipeline.resume_runner import run_pipeline_stream_resume
from backend.App.orchestration.application.pipeline.retry_runner import run_pipeline_stream_retry
from backend.App.orchestration.application.pipeline.staged_runner import run_pipeline_stream_staged
from backend.App.orchestration.application.pipeline.step_lifecycle import (
    checkpoint_session,
    complete_session,
    emit_step_end_trace,
    emit_step_error_trace,
    emit_step_start_trace,
    index_step_state,
    mark_task_done_with_contract_validator,
    register_dev_step_artifacts,
    register_step_complete_with_contract_validator,
    register_step_error_with_contract_validator,
    register_step_start_with_contract_validator,
)
from backend.App.orchestration.application.pipeline.step_output_extractor import StepOutputExtractor
from backend.App.orchestration.application.pipeline.step_quality_checks import run_all_post_step_quality_checks
from backend.App.orchestration.application.pipeline.task_class_router import auto_select_pipeline_steps
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled
from backend.App.orchestration.domain.pipeline_machine import get_pipeline_machine, reset_pipeline_machine
from backend.App.orchestration.infrastructure.step_stream_executor import StepStreamExecutor
from backend.App.shared.application.trace_emitter import emit_trace_event

from backend.App.orchestration.application.enforcement.pipeline_enforcement import (  # noqa: F401  TODO(size-budget)
    _CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY,
    _planning_review_resume_step,
    _should_block_for_human,
    enforce_planning_review_gate as _enforce_planning_review_gate,
    enter_fix_cycle_or_escalate as _enter_fix_cycle_or_escalate,
    finalize_pipeline_machine as _finalize_pipeline_machine,
    require_structured_blockers as _require_structured_blockers,
    run_post_dev_verification_gates as _run_post_dev_verification_gates,
    sync_pipeline_machine as _sync_pipeline_machine,
    transition_pipeline_phase as _transition_pipeline_phase,
)

_logger = logging.getLogger(__name__)

_step_executor = StepStreamExecutor()
_step_extractor = StepOutputExtractor()

__all__ = (
    "run_pipeline_stream",
    "run_pipeline_stream_resume",
    "run_pipeline_stream_retry",
    "run_pipeline_stream_staged",
    "_record_open_defects",
    "_CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY",
    "_planning_review_resume_step",
    "_enforce_planning_review_gate",
    "_enter_fix_cycle_or_escalate",
    "_finalize_pipeline_machine",
    "_require_structured_blockers",
    "_run_post_dev_verification_gates",
    "_should_block_for_human",
    "_sync_pipeline_machine",
    "_transition_pipeline_phase",
)


def run_pipeline_stream(
    user_input: str,
    agent_config: Optional[dict[str, Any]] = None,
    pipeline_steps: Optional[list[str]] = None,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    cancel_event: Optional[threading.Event] = None,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
    _ring_pass: int = 0,
    _ring_initial_state: Optional[Any] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    from backend.App.orchestration.application.routing.pipeline_graph import (
        DEFAULT_PIPELINE_STEP_IDS,
        _compact_state_if_needed,
        _initial_pipeline_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _state_snapshot,
        validate_pipeline_steps,
    )

    base_agent_config = agent_config or {}
    selected_steps = (
        pipeline_steps
        if pipeline_steps is not None
        else auto_select_pipeline_steps(user_input, base_agent_config, DEFAULT_PIPELINE_STEP_IDS)
    )
    validate_pipeline_steps(selected_steps, base_agent_config)
    reset_pipeline_machine()
    machine = get_pipeline_machine()

    if task_id:
        from backend.App.orchestration.domain.contract_validator import ContractViolation
        from backend.App.orchestration.infrastructure.runtime_policy import get_runtime_validator
        try:
            get_runtime_validator().register_task(task_id, "orchestrator")
        except ContractViolation as contract_violation:
            _logger.debug(
                "pipeline_runners: register_task contract violation for task=%s: %s",
                task_id, contract_violation,
            )

    topology = (base_agent_config.get("swarm") or {}).get("topology", "") if isinstance(base_agent_config, dict) else ""
    if topology and topology not in ("", "linear", "default") and pipeline_steps is None:
        final_state: PipelineState = yield from run_pipeline_stream_via_graph(
            user_input, base_agent_config, workspace_root, workspace_apply_writes,
            task_id, cancel_event, topology,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=selected_steps,
        )
        return final_state

    warning_step_ids = pipeline_step_ids if pipeline_step_ids is not None else selected_steps
    if _ring_initial_state is not None:
        state = _ring_initial_state
        set_ephemeral(state, "input", user_input)
        set_ephemeral(state, "_pipeline_step_ids", list(selected_steps))
        set_ephemeral(state, "open_defects", [])
        set_ephemeral(state, "clustered_open_defects", [])
        set_ephemeral(state, "_needs_work_count", 0)
    else:
        state = _initial_pipeline_state(
            user_input, base_agent_config,
            workspace_root=workspace_root,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            cancel_event=cancel_event,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=warning_step_ids,
        )
        set_ephemeral(state, "_pipeline_step_ids", list(selected_steps))
    sync_pipeline_machine(state, machine)

    if _ring_initial_state is None:
        from backend.App.integrations.infrastructure.mcp.auto.auto import format_mcp_auto_status_line
        from backend.App.orchestration.application.routing.step_order_analyzer import (
            analyze_pipeline_step_order,
        )
        order_report = analyze_pipeline_step_order(selected_steps)
        if order_report.has_violations:
            yield {
                "agent": "orchestrator",
                "status": "warning",
                "message": (
                    "Pipeline step order issues detected — running anyway:\n"
                    f"{order_report.format_summary()}"
                ),
                "step_order_violations": [
                    {
                        "step_id": violation.step_id,
                        "step_index": violation.step_index,
                        "missing_prerequisite": violation.missing_prerequisite,
                        "prerequisite_index": violation.prerequisite_index,
                    }
                    for violation in order_report.violations
                ],
            }
        mcp_config_for_status = state.get("agent_config") or {}
        mcp_status_summary = (
            mcp_config_for_status.get("mcp", {}).get("status_summary")
            if isinstance(mcp_config_for_status, dict) else None
        )
        if isinstance(mcp_status_summary, dict):
            mcp_status_line = format_mcp_auto_status_line(mcp_status_summary)
            if mcp_status_line:
                yield {
                    "agent": "orchestrator",
                    "status": "progress",
                    "message": mcp_status_line,
                }

    session_id: str | None = None
    session_manager = None
    trace_collector = None
    try:
        from backend.App.orchestration.infrastructure._singletons import get_session_manager, get_trace_collector
        session_manager = get_session_manager()
        trace_collector = get_trace_collector()
        session = session_manager.create_session(task_id, metadata={"steps": list(selected_steps)})
        session_id = session.session_id
        set_ephemeral(state, "_session_id", session_id)
        emit_trace_event(trace_collector, task_id, session_id, "pipeline", "run_start", {"steps": list(selected_steps)})
    except Exception as session_init_error:
        _logger.debug("Session/trace init skipped: %s", session_init_error)

    try:
        for step_id in selected_steps:
            if _pipeline_should_cancel(state):
                raise PipelineCancelled("pipeline cancelled (client disconnect or server shutdown)")
            prepare_pipeline_machine_for_step(state, machine, step_id)
            compaction_event = _compact_state_if_needed(state, step_id)
            if compaction_event is not None:
                yield compaction_event
            preflight_event = enforce_workspace_preflight(state, step_id)
            if preflight_event is not None:
                yield preflight_event
            _, step_func = _resolve_pipeline_step(step_id, base_agent_config)
            yield {"agent": step_id, "status": "in_progress", "message": pipeline_step_in_progress_message(step_id, state)}
            register_step_start_with_contract_validator(task_id, step_id)
            step_event_id = emit_step_start_trace(trace_collector, task_id, session_id or "", step_id)
            try:
                yield from _step_executor.run(step_id, step_func, state)
            except HumanApprovalRequired as exc:
                from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                    finalize_metrics_best_effort,
                )
                finalize_metrics_best_effort(state)
                exc.partial_state = _state_snapshot(state)
                if not exc.resume_pipeline_step:
                    exc.resume_pipeline_step = step_id
                if session_manager is not None and session_id:
                    try:
                        from backend.App.orchestration.domain.session import SessionStatus
                        session_manager._update_status(session_id, SessionStatus.PAUSED)
                    except Exception as session_pause_error:
                        _logger.debug(
                            "pipeline_runners: session_manager pause failed during "
                            "human-approval cleanup for step=%s: %s",
                            step_id, session_pause_error,
                        )
                raise
            except PipelineCancelled:
                raise
            except Exception as exc:
                from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                    finalize_metrics_best_effort,
                )
                from backend.App.orchestration.application.pipeline.ring_failure_restart import (
                    try_ring_restart_after_failure,
                )
                finalize_metrics_best_effort(state)
                setattr(exc, "_partial_state", _state_snapshot(state))
                setattr(exc, "_failed_step", step_id)
                register_step_error_with_contract_validator(task_id, step_id, str(exc))
                emit_step_error_trace(trace_collector, task_id, session_id or "", step_id, step_event_id, str(exc))
                ring_final = yield from try_ring_restart_after_failure(
                    cast(dict[str, Any], state),
                    user_input=user_input,
                    base_agent_config=base_agent_config,
                    pipeline_steps=list(selected_steps),
                    workspace_root=workspace_root,
                    workspace_apply_writes=workspace_apply_writes,
                    task_id=task_id,
                    cancel_event=cancel_event,
                    pipeline_workspace_parts=pipeline_workspace_parts,
                    ring_pass=_ring_pass,
                    failed_step=step_id,
                    exception=exc,
                )
                if ring_final is not None:
                    return ring_final
                if session_manager is not None and session_id:
                    try:
                        session_manager.fail_session(session_id, reason=str(exc)[:500])
                    except Exception as session_fail_error:
                        _logger.debug(
                            "pipeline_runners: session_manager.fail_session failed during "
                            "step-exception cleanup for step=%s: %s",
                            step_id, session_fail_error,
                        )
                raise
            yield _step_extractor.emit_completed(step_id, state)
            index_step_state(state)
            register_dev_step_artifacts(state, step_id)
            register_step_complete_with_contract_validator(task_id, step_id)
            emit_step_end_trace(trace_collector, task_id, session_id or "", step_id, step_event_id)
            checkpoint_session(session_manager, session_id or "", step_id, task_id)

            try:
                yield from _run_post_step_enforcement(
                    state, machine, step_id, base_agent_config,
                    _resolve_pipeline_step, _step_executor.run, _step_extractor.emit_completed,
                )
            except (HumanApprovalRequired, PipelineCancelled):
                raise
            except Exception as enforcement_exception:
                from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                    finalize_metrics_best_effort,
                )
                finalize_metrics_best_effort(state)
                setattr(enforcement_exception, "_partial_state", _state_snapshot(state))
                setattr(enforcement_exception, "_failed_step", step_id)
                register_step_error_with_contract_validator(task_id, step_id, str(enforcement_exception))
                emit_step_error_trace(
                    trace_collector, task_id, session_id or "", step_id,
                    step_event_id, str(enforcement_exception),
                )
                if session_manager is not None and session_id:
                    try:
                        session_manager.fail_session(session_id, reason=str(enforcement_exception)[:500])
                    except Exception as session_fail_error:
                        _logger.debug(
                            "pipeline_runners: session_manager.fail_session failed during "
                            "enforcement-exception cleanup for step=%s: %s",
                            step_id, session_fail_error,
                        )
                raise

            try:
                should_stop = yield from run_all_post_step_quality_checks(
                    step_id, state, base_agent_config,
                    _step_executor, _step_extractor, _resolve_pipeline_step,
                )
            except (HumanApprovalRequired, PipelineCancelled):
                raise
            except Exception as quality_check_exception:
                from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                    finalize_metrics_best_effort,
                )
                finalize_metrics_best_effort(state)
                setattr(quality_check_exception, "_partial_state", _state_snapshot(state))
                setattr(quality_check_exception, "_failed_step", step_id)
                register_step_error_with_contract_validator(task_id, step_id, str(quality_check_exception))
                emit_step_error_trace(
                    trace_collector, task_id, session_id or "", step_id,
                    step_event_id, str(quality_check_exception),
                )
                if session_manager is not None and session_id:
                    try:
                        session_manager.fail_session(session_id, reason=str(quality_check_exception)[:500])
                    except Exception as session_fail_error:
                        _logger.debug(
                            "pipeline_runners: session_manager.fail_session failed during "
                            "quality-check-exception cleanup for step=%s: %s",
                            step_id, session_fail_error,
                        )
                raise
            if should_stop:
                break

            mark_task_done_with_contract_validator(task_id)
            complete_session(session_manager, session_id)
    finally:
        if trace_collector is not None and session_id:
            emit_trace_event(trace_collector, task_id, session_id, "pipeline", "run_end", {})

    finalize_pipeline_machine(state, machine)
    from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
        finalize_pipeline_metrics,
    )
    finalize_pipeline_metrics(state)

    from backend.App.orchestration.application.pipeline.ring_restart_check import (
        build_ring_restart_defect_context,
        build_ring_restart_event,
        evaluate_ring_restart,
    )
    ring_evaluation = evaluate_ring_restart(
        cast(dict[str, Any], state), topology, pipeline_steps, _ring_pass,
    )
    if ring_evaluation["should_restart"]:
        defect_context = build_ring_restart_defect_context(ring_evaluation)
        yield build_ring_restart_event(ring_evaluation)
        ring_final: PipelineState = yield from run_pipeline_stream(
            user_input + defect_context,
            agent_config=base_agent_config,
            pipeline_steps=list(selected_steps),
            workspace_root=workspace_root,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            cancel_event=cancel_event,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=list(selected_steps),
            _ring_pass=_ring_pass + 1,
            _ring_initial_state=dict(state),
        )
        return ring_final

    return state
