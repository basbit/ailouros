from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Generator
from typing import Any, Optional, cast

from backend.App.orchestration.application.enforcement.machine_transitions import (
    finalize_pipeline_machine,
    prepare_pipeline_machine_for_step,
    sync_pipeline_machine,
)
from backend.App.orchestration.application.enforcement.pipeline_enforcement import run_post_step_enforcement
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine
from backend.App.orchestration.infrastructure.step_stream_executor import StepStreamExecutor
from backend.App.orchestration.application.pipeline.step_output_extractor import StepOutputExtractor

_logger = logging.getLogger(__name__)

_step_executor = StepStreamExecutor()
_step_extractor = StepOutputExtractor()


def run_pipeline_stream_retry(
    partial_state: PipelineState,
    pipeline_steps: list[str],
    from_step: str,
    override_agent_config: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    from backend.App.orchestration.application.routing.pipeline_graph import (
        _migrate_legacy_pm_tasks_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _state_snapshot,
        validate_pipeline_steps,
    )
    from backend.App.orchestration.application.pipeline.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    state: dict[str, Any] = copy.deepcopy(cast(dict[str, Any], partial_state))
    _migrate_legacy_pm_tasks_state(state)
    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    if cancel_event is not None:
        state["_pipeline_cancel_event"] = cancel_event

    if isinstance(override_agent_config, dict) and override_agent_config:
        merged_config: dict[str, Any] = dict(state.get("agent_config") or {})
        for config_key, config_value in override_agent_config.items():
            if isinstance(config_value, dict) and isinstance(merged_config.get(config_key), dict):
                merged_config[config_key] = {**merged_config[config_key], **config_value}
            else:
                merged_config[config_key] = config_value
        state["agent_config"] = merged_config

    retry_agent_config = state.get("agent_config") or {}
    validate_pipeline_steps(pipeline_steps, retry_agent_config)

    try:
        step_index = pipeline_steps.index(from_step)
    except ValueError as exc:
        raise ValueError(f"Step {from_step!r} not found in pipeline_steps") from exc

    state["_pipeline_step_ids"] = list(pipeline_steps)
    sync_pipeline_machine(state, machine)

    for step_id in pipeline_steps[step_index:]:
        if _pipeline_should_cancel(state):
            raise PipelineCancelled("pipeline cancelled (client disconnect or server shutdown)")
        prepare_pipeline_machine_for_step(state, machine, step_id)
        _, step_func = _resolve_pipeline_step(step_id, retry_agent_config)
        yield {"agent": step_id, "status": "in_progress", "message": pipeline_step_in_progress_message(step_id, state)}
        try:
            yield from _step_executor.run(step_id, step_func, state)
        except HumanApprovalRequired as exc:
            from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                finalize_metrics_best_effort,
            )
            finalize_metrics_best_effort(cast(PipelineState, state))
            exc.partial_state = _state_snapshot(state)
            if not exc.resume_pipeline_step:
                exc.resume_pipeline_step = step_id
            raise
        except PipelineCancelled:
            raise
        except Exception as exc:
            from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                finalize_metrics_best_effort,
            )
            finalize_metrics_best_effort(cast(PipelineState, state))
            setattr(exc, "_partial_state", _state_snapshot(state))
            setattr(exc, "_failed_step", step_id)
            raise
        yield _step_extractor.emit_completed(step_id, state)
        try:
            yield from run_post_step_enforcement(
                state, machine, step_id, retry_agent_config,
                _resolve_pipeline_step, _step_executor.run, _step_extractor.emit_completed,
            )
        except (HumanApprovalRequired, PipelineCancelled):
            raise
        except Exception as enforcement_exception:
            from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
                finalize_metrics_best_effort,
            )
            finalize_metrics_best_effort(cast(PipelineState, state))
            setattr(enforcement_exception, "_partial_state", _state_snapshot(state))
            setattr(enforcement_exception, "_failed_step", step_id)
            raise

    finalize_pipeline_machine(state, machine)
    from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
        finalize_pipeline_metrics,
    )
    finalize_pipeline_metrics(cast(PipelineState, state))

    from backend.App.orchestration.application.pipeline.ring_restart_check import (
        build_ring_restart_defect_context,
        build_ring_restart_event,
        evaluate_ring_restart,
        topology_from_agent_config,
    )
    retry_agent_config_raw = override_agent_config if isinstance(override_agent_config, dict) and override_agent_config else state.get("agent_config")
    retry_agent_config: dict[str, Any] = retry_agent_config_raw if isinstance(retry_agent_config_raw, dict) else {}
    topology = topology_from_agent_config(retry_agent_config)
    ring_pass_value = int(state.get("_ring_pass") or 0)
    ring_evaluation = evaluate_ring_restart(
        state, topology, pipeline_steps, ring_pass_value,
    )
    if ring_evaluation["should_restart"]:
        from backend.App.orchestration.application.pipeline.pipeline_runners import (
            run_pipeline_stream,
        )
        defect_context = build_ring_restart_defect_context(ring_evaluation)
        yield build_ring_restart_event(ring_evaluation)
        user_input_for_restart = str(state.get("input") or state.get("user_task") or "")
        workspace_root_value = str(state.get("workspace_root") or "")
        workspace_apply_writes_value = bool(state.get("workspace_apply_writes"))
        task_id_value = str(state.get("task_id") or "")
        ring_final: PipelineState = yield from run_pipeline_stream(
            user_input_for_restart + defect_context,
            agent_config=retry_agent_config,
            pipeline_steps=list(pipeline_steps),
            workspace_root=workspace_root_value,
            workspace_apply_writes=workspace_apply_writes_value,
            task_id=task_id_value,
            cancel_event=cancel_event,
            pipeline_step_ids=list(pipeline_steps),
            _ring_pass=ring_pass_value + 1,
            _ring_initial_state=dict(state),
        )
        return ring_final

    return cast(PipelineState, state)
