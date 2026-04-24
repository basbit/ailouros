from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Generator
from typing import Any, Optional, cast

from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    finalize_pipeline_metrics,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.pipeline.runners_policy import node_to_step_map
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled

_logger = logging.getLogger(__name__)


def run_pipeline_stream_via_graph(
    user_input: str,
    agent_config: dict[str, Any],
    workspace_root: str,
    workspace_apply_writes: bool,
    task_id: str,
    cancel_event: Optional[threading.Event],
    topology: str,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    from backend.App.orchestration.application.routing.graph_builder import PipelineGraphBuilder
    from backend.App.orchestration.application.pipeline.pipeline_state_helpers import _initial_pipeline_state

    _logger.info("Using LangGraph graph for topology=%r (stream mode)", topology)
    compiled = PipelineGraphBuilder().build_for_topology(topology, agent_config)
    initial_state = _initial_pipeline_state(
        user_input,
        agent_config,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=pipeline_step_ids,
    )
    if pipeline_step_ids:
        cast(dict, initial_state)["_pipeline_step_ids"] = list(pipeline_step_ids)

    if workspace_root:
        from backend.App.workspace.application.wiki.wiki_context_loader import (
            load_wiki_context,
            query_for_pipeline_step,
        )
        try:
            initial_query = query_for_pipeline_step(initial_state, "pm")
            wiki_context = load_wiki_context(workspace_root, query=initial_query or None)
        except (OSError, ValueError, RuntimeError):
            _logger.exception("wiki context load failed for workspace %r", workspace_root)
            wiki_context = ""
        if wiki_context:
            initial_state["wiki_context"] = wiki_context

    final_state: PipelineState = cast(PipelineState, dict(initial_state))
    node_step_mapping: dict[str, str] = node_to_step_map()
    seen_nodes: set[str] = set()

    try:
        for event in compiled.stream(initial_state, config={"recursion_limit": 96}):
            if cancel_event is not None and cancel_event.is_set():
                raise PipelineCancelled("pipeline cancelled (client disconnect or server shutdown)")
            for node_name, updates in event.items():
                if not isinstance(updates, dict):
                    continue
                agent_name = node_step_mapping.get(node_name, node_name.lower())
                if node_name not in seen_nodes:
                    seen_nodes.add(node_name)
                    yield {"agent": agent_name, "status": "in_progress", "message": f"{agent_name} started"}
                cast(dict, final_state).update(updates)
                output_key = next(
                    (k for k in updates if k.endswith("_output") and isinstance(updates[k], str)),
                    None,
                )
                if output_key:
                    yield {
                        "agent": agent_name,
                        "status": "completed",
                        "message": str(updates[output_key])[:500],
                        "model": updates.get(output_key.replace("_output", "_model"), ""),
                        "provider": updates.get(output_key.replace("_output", "_provider"), ""),
                    }
                else:
                    yield {"agent": agent_name, "status": "completed", "message": ""}
    except HumanApprovalRequired:
        raise
    except PipelineCancelled:
        raise
    except Exception as exc:
        setattr(exc, "_partial_state", copy.deepcopy(final_state))
        setattr(exc, "_failed_step", "graph")
        raise

    finalize_pipeline_metrics(final_state)
    return final_state
