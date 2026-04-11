"""PipelineRunner — synchronous (non-streaming) pipeline execution.

Extracted from ``pipeline_graph.py`` (DECOMP-10).

Streaming variants (run_pipeline_stream, run_pipeline_stream_resume,
run_pipeline_stream_retry) remain in ``pipeline_runners.py``.

Backward-compat: ``pipeline_graph.run_pipeline`` delegates here.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Executes a linear pipeline synchronously (blocking, no SSE).

    Delegates graph building to :class:`PipelineGraphBuilder` and step
    resolution to ``_resolve_pipeline_step`` from ``pipeline_graph``.

    Usage::

        runner = PipelineRunner()
        result = runner.run(
            user_input="build a landing page",
            agent_config={},
            pipeline_steps=["pm", "ba", "qa"],
        )
    """

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
        """Run the pipeline and return the final state.

        When ``pipeline_steps`` is ``None``, the full LangGraph compiled graph
        is used (fan-out BA ∥ ARCH topology).  When ``pipeline_steps`` is a
        list, each step is run sequentially in the given order.

        Args:
            user_input: Effective user prompt (workspace-assembled).
            agent_config: Merged agent configuration dict.
            pipeline_steps: Explicit ordered step list, or None for full graph.
            workspace_root: Absolute path to workspace root.
            workspace_apply_writes: Whether to apply file writes post-pipeline.
            task_id: Task identifier for logging and artifact storage.
            pipeline_workspace_parts: Pre-assembled workspace metadata dict.
            pipeline_step_ids: Step IDs for warning/display (may differ from
                pipeline_steps when using full graph).

        Returns:
            Final ``PipelineState`` after all steps complete.

        Raises:
            HumanApprovalRequired: When a human gate is reached.
        """
        # Import lazily to avoid circular import with pipeline_graph
        from backend.App.orchestration.application.pipeline_graph import (
            DEFAULT_PIPELINE_STEP_IDS,
            _resolve_pipeline_step,
            _state_snapshot,
            validate_pipeline_steps,
        )
        from backend.App.orchestration.application.pipeline_state_helpers import (
            _initial_pipeline_state,
        )
        from backend.App.orchestration.application.graph_builder import PipelineGraphBuilder

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
                agent_config,
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
            agent_config,
            workspace_root=workspace_root,
            workspace_apply_writes=workspace_apply_writes,
            task_id=task_id,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=step_ids_for_warn,
        )
        for step_id in pipeline_steps:
            _, step_func = _resolve_pipeline_step(step_id, agent_config)
            try:
                state.update(step_func(state))
            except HumanApprovalRequired as exc:
                exc.partial_state = _state_snapshot(state)
                exc.resume_pipeline_step = step_id
                raise
        return state


# Module-level convenience function that mirrors the existing ``run_pipeline``
# signature exactly — used by ``pipeline_graph.run_pipeline`` as its
# implementation after delegation.
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
    """Convenience wrapper around ``PipelineRunner.run()``.

    Kept for callers that prefer the functional style.
    ``pipeline_graph.run_pipeline`` delegates here (backward compat).
    """
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
