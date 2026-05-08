from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from typing import Any, Optional

from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
    record_ring_unresolved_escalation,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.pipeline.ring_restart_check import (
    build_ring_restart_defect_context,
    build_ring_restart_event,
    evaluate_ring_restart,
    ring_max_restarts_default,
    topology_from_agent_config,
)

_logger = logging.getLogger(__name__)


def _record_step_exception_as_escalation(
    state: dict[str, Any], *, failed_step: str, exception: BaseException,
) -> None:
    record_ring_unresolved_escalation(
        state,
        step_id=failed_step,
        verdict="EXCEPTION",
        retries=0,
        max_retries=ring_max_restarts_default(),
        reason=f"step {failed_step} raised {type(exception).__name__}: {str(exception)[:200]}",
    )


def try_ring_restart_after_failure(
    state: dict[str, Any],
    *,
    user_input: str,
    base_agent_config: dict[str, Any],
    pipeline_steps: list[str],
    workspace_root: str,
    workspace_apply_writes: bool,
    task_id: str,
    cancel_event: Optional[threading.Event],
    pipeline_workspace_parts: Optional[dict[str, Any]],
    ring_pass: int,
    failed_step: str,
    exception: BaseException,
) -> Generator[dict[str, Any], None, Optional[PipelineState]]:
    topology = topology_from_agent_config(base_agent_config)
    if topology != "ring":
        return None
    if ring_pass >= ring_max_restarts_default():
        return None

    _record_step_exception_as_escalation(state, failed_step=failed_step, exception=exception)

    ring_evaluation = evaluate_ring_restart(state, topology, list(pipeline_steps), ring_pass)
    if not ring_evaluation["should_restart"]:
        return None

    _logger.info(
        "Ring restart after failure: step=%s ring_pass=%d/%d error=%s",
        failed_step, ring_pass, ring_evaluation["ring_max_restarts"],
        type(exception).__name__,
    )

    from backend.App.orchestration.application.pipeline.pipeline_runners import (
        run_pipeline_stream,
    )

    defect_context = build_ring_restart_defect_context(ring_evaluation)
    yield build_ring_restart_event(ring_evaluation)
    ring_final: PipelineState = yield from run_pipeline_stream(
        user_input + defect_context,
        agent_config=base_agent_config,
        pipeline_steps=list(pipeline_steps),
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=list(pipeline_steps),
        _ring_pass=ring_pass + 1,
        _ring_initial_state=dict(state),
    )
    return ring_final
