from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Generator
from typing import Any, Optional

from backend.App.orchestration.application.enforcement.machine_transitions import (
    finalize_pipeline_machine,
    prepare_pipeline_machine_for_step,
    sync_pipeline_machine,
)
from backend.App.orchestration.application.enforcement.pipeline_enforcement import run_post_step_enforcement
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    set_ephemeral,
    update_ephemeral,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase
from backend.App.orchestration.infrastructure.step_stream_executor import StepStreamExecutor
from backend.App.orchestration.application.pipeline.step_output_extractor import StepOutputExtractor

_logger = logging.getLogger(__name__)

_step_executor = StepStreamExecutor()
_step_extractor = StepOutputExtractor()


def run_pipeline_stream_staged(
    user_input: str,
    pipeline_stages: list[list[str]],
    agent_config: Optional[dict[str, Any]] = None,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    cancel_event: Optional[threading.Event] = None,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    from backend.App.orchestration.application.routing.pipeline_graph import (
        _compact_state_if_needed,
        _initial_pipeline_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _state_snapshot,
    )
    from backend.App.orchestration.application.pipeline.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    base_agent_config = agent_config or {}
    all_step_ids = [step_id for stage in pipeline_stages for step_id in stage]
    machine = PipelineMachine()

    state = _initial_pipeline_state(
        user_input,
        base_agent_config,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=all_step_ids,
    )
    set_ephemeral(state, "_pipeline_step_ids", list(all_step_ids))
    sync_pipeline_machine(state, machine)

    for stage_index, stage in enumerate(pipeline_stages):
        if _pipeline_should_cancel(state):
            raise PipelineCancelled("pipeline cancelled (client disconnect or server shutdown)")

        if len(stage) == 1:
            step_id = stage[0]
            prepare_pipeline_machine_for_step(state, machine, step_id)
            compaction_event = _compact_state_if_needed(state, step_id)
            if compaction_event is not None:
                yield compaction_event
            _, step_func = _resolve_pipeline_step(step_id, base_agent_config)
            yield {"agent": step_id, "status": "in_progress", "message": pipeline_step_in_progress_message(step_id, state)}
            try:
                yield from _step_executor.run(step_id, step_func, state)
            except HumanApprovalRequired as exc:
                exc.partial_state = _state_snapshot(state)
                if not exc.resume_pipeline_step:
                    exc.resume_pipeline_step = step_id
                raise
            except PipelineCancelled:
                raise
            except Exception as exc:
                setattr(exc, "_partial_state", _state_snapshot(state))
                setattr(exc, "_failed_step", step_id)
                raise
            yield _step_extractor.emit_completed(step_id, state)
            yield from run_post_step_enforcement(
                state, machine, step_id, base_agent_config,
                _resolve_pipeline_step, _step_executor.run, _step_extractor.emit_completed,
            )
        else:
            active_steps = list(stage)
            non_plan_steps = [
                step_id for step_id in active_steps
                if machine.step_phase(step_id) != PipelinePhase.PLAN
            ]
            if non_plan_steps:
                raise ValueError(
                    "Parallel staged execution is only supported for PLAN-phase steps; "
                    f"got non-PLAN steps in one stage: {non_plan_steps}"
                )
            yield {
                "type": "active_steps",
                "activeSteps": active_steps,
                "stage": stage_index,
                "status": "in_progress",
                "message": f"Running parallel stage: {', '.join(active_steps)}",
            }
            for step_id in active_steps:
                yield {
                    "agent": step_id,
                    "status": "in_progress",
                    "message": pipeline_step_in_progress_message(step_id, state),
                }

            step_results: dict[str, dict[str, Any]] = {}
            step_errors: dict[str, Exception] = {}

            def run_parallel_step(parallel_step_id: str) -> dict[str, Any]:
                _, parallel_step_func = _resolve_pipeline_step(parallel_step_id, base_agent_config)
                return parallel_step_func(state)

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_steps)) as executor:
                future_to_step = {executor.submit(run_parallel_step, step_id): step_id for step_id in active_steps}
                for future in concurrent.futures.as_completed(future_to_step):
                    parallel_step_id = future_to_step[future]
                    try:
                        step_results[parallel_step_id] = future.result()
                    except Exception as parallel_error:
                        step_errors[parallel_step_id] = parallel_error

            for step_id in active_steps:
                if step_id in step_results:
                    update_ephemeral(state, step_results[step_id])
                    yield _step_extractor.emit_completed(step_id, state)

            if step_errors:
                first_error_step = next(iter(step_errors))
                first_exception = step_errors[first_error_step]
                setattr(first_exception, "_partial_state", _state_snapshot(state))
                setattr(first_exception, "_failed_step", first_error_step)
                raise first_exception

        if state.get("_pipeline_stop_early"):
            stop_reason = state.get("_pipeline_stop_reason") or ""
            if stop_reason:
                yield {"agent": stage[0], "status": "warning", "message": stop_reason}
            break

    finalize_pipeline_machine(state, machine)
    return state
