
from __future__ import annotations

import logging
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline.ephemeral_state import update_ephemeral
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

logger = logging.getLogger(__name__)


class PipelineRunner:

    def run(
        self,
        user_input: str,
        agent_config: Optional[dict[str, Any]] = None,
        pipeline_steps: Optional[list[str]] = None,
        workspace_root: str = "",
        workspace_apply_writes: bool = False,
        task_id: str = "",
        *,
        pipeline_workspace_parts: Optional[dict[str, Any]] = None,
        pipeline_step_ids: Optional[list[str]] = None,
    ) -> PipelineState:
        from backend.App.orchestration.application.routing.pipeline_graph import (
            DEFAULT_PIPELINE_STEP_IDS,
            _resolve_pipeline_step,
            _state_snapshot,
            validate_pipeline_steps,
        )
        from backend.App.orchestration.application.pipeline.pipeline_state_helpers import (
            _initial_pipeline_state,
        )
        from backend.App.orchestration.application.routing.graph_builder import PipelineGraphBuilder

        step_ids_for_warn = (
            pipeline_step_ids
            if pipeline_step_ids is not None
            else (pipeline_steps if pipeline_steps is not None else DEFAULT_PIPELINE_STEP_IDS)
        )

        if pipeline_steps is None:
            topology = (agent_config or {}).get("swarm", {}).get("topology", "")
            compiled = PipelineGraphBuilder().build_for_topology(topology, agent_config)
            init = _initial_pipeline_state(
                user_input,
                agent_config or {},
                workspace_root=workspace_root,
                workspace_apply_writes=workspace_apply_writes,
                task_id=task_id,
                pipeline_workspace_parts=pipeline_workspace_parts,
                pipeline_step_ids=step_ids_for_warn,
            )
            result: dict[str, Any] = compiled.invoke(
                init, config={"recursion_limit": 96}
            )
            return cast(PipelineState, result)

        validate_pipeline_steps(pipeline_steps, agent_config)
        state = _initial_pipeline_state(
            user_input,
            agent_config or {},
            workspace_root=workspace_root,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=step_ids_for_warn,
        )
        for step_id in pipeline_steps:
            _, step_func = _resolve_pipeline_step(step_id, agent_config)
            try:
                update_ephemeral(state, step_func(state))
            except HumanApprovalRequired as exc:
                exc.partial_state = _state_snapshot(state)
                exc.resume_pipeline_step = step_id
                raise
        return state


def run_pipeline(
    user_input: str,
    agent_config: Optional[dict[str, Any]] = None,
    pipeline_steps: Optional[list[str]] = None,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
) -> PipelineState:
    return PipelineRunner().run(
        user_input=user_input,
        agent_config=agent_config,
        pipeline_steps=pipeline_steps,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=pipeline_step_ids,
    )
