
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from backend.App.orchestration.application.pipeline.pipeline_state import (
    ARTIFACT_AGENT_OUTPUT_KEYS,
    PipelineState,
)
from backend.App.orchestration.domain.quality_gate_policy import extract_verdict

from backend.App.orchestration.application.routing.graph_builder import (
    _with_approval_gate,
    _dev_review_router,
    _dev_retry_gate_node,
    _qa_review_router,
)
from backend.App.orchestration.application.nodes._shared import _pipeline_should_cancel
from backend.App.orchestration.application.nodes._shared import (
    _code_analysis_is_weak,
    _documentation_product_context_block,
    _effective_spec_for_build,
    _effective_spec_block_for_doc_chain,
    _spec_arch_context_for_docs,
    _remote_api_client_kwargs,
    _remote_api_client_kwargs_for_role,
)
from backend.App.orchestration.infrastructure.agents.code_workflow_agents import (
    CodeDiagramAgent,
    DocGenerateAgent,
    ProblemSpotterAgent,
    RefactorPlanAgent,
)
from backend.App.orchestration.infrastructure.agents.custom_agent import CustomSwarmRoleAgent
from backend.App.orchestration.application.nodes.pm import (
    clarify_input_node,
    human_clarify_input_node,
    pm_node,
    review_pm_node,
    human_pm_node,
)
from backend.App.orchestration.application.nodes.ba import ba_node, review_ba_node, human_ba_node
from backend.App.orchestration.application.nodes.arch import (
    arch_node,
    review_stack_node,
    review_arch_node,
    human_arch_node,
    ba_arch_debate_node,
    merge_spec_node,
    review_spec_node,
    human_spec_node,
)
from backend.App.orchestration.application.nodes.documentation import (
    analyze_code_node,
    generate_documentation_node,
    problem_spotter_node,
    refactor_plan_node,
    human_code_review_node,
)
from backend.App.orchestration.application.nodes.devops import devops_node, review_devops_node, human_devops_node
from backend.App.orchestration.application.nodes.dev import (
    parse_dev_qa_task_plan,
    read_dev_qa_task_count_target,
    normalize_dev_qa_tasks_to_count,
    dev_lead_node,
    review_dev_lead_node,
    human_dev_lead_node,
    dev_node,
    review_dev_node,
    human_dev_node,
)
from backend.App.orchestration.application.nodes.qa import qa_node, review_qa_node, human_qa_node
from backend.App.orchestration.application.nodes.custom import (
    custom_role_step_id,
    parse_custom_role_slug,
    _make_custom_role_node,
)
from backend.App.orchestration.application.routing.step_registry import (
    PIPELINE_STEP_SEQUENCE,
    PIPELINE_STEP_REGISTRY,
    DEFAULT_PIPELINE_STEP_IDS,
    PipelineStepRegistry,
    validate_pipeline_steps as _validate_pipeline_steps_from_registry,
)
from backend.App.orchestration.application.pipeline.step_decorator import hook_wrap as _hook_wrap
from backend.App.orchestration.application.pipeline.pipeline_state_helpers import (
    HUMAN_PIPELINE_STEP_TO_STATE_KEY,
    _ASSEMBLED_USER_TASK_MARKER,
    _COMPACTION_KEEP_KEYS,
    _COMPACTION_SUMMARISE_KEYS,
    _RUNTIME_STATE_KEYS,
    _compact_state_if_needed,
    _initial_pipeline_state,
    _legacy_workspace_parts_from_input,
    _migrate_legacy_pm_tasks_state,
    _state_max_chars,
    _state_snapshot,
    format_human_resume_output,
    human_pipeline_step_label,
)
from backend.App.orchestration.application.routing.graph_builder import PipelineGraphBuilder as _PipelineGraphBuilder
from backend.App.orchestration.application.pipeline.pipeline_runner import (
    PipelineRunner as _PipelineRunner,
    run_pipeline as _run_pipeline_impl,
)
from backend.App.orchestration.application.pipeline.pipeline_step_runner import (
    _format_elapsed_wall,
    _stream_progress_heartbeat_seconds,
    final_pipeline_user_message,
    primary_output_for_step,
    task_store_agent_label,
)
from backend.App.orchestration.application.pipeline.pipeline_runners import (
    run_pipeline_stream,
    run_pipeline_stream_resume,
    run_pipeline_stream_retry,
)

logger = logging.getLogger(__name__)


def _quality_gate_router(state: dict, step_id: str) -> str:
    from backend.App.orchestration.application.routing.graph_builder import (
        _quality_gate_env_default,
        _max_step_retries_env,
    )
    from backend.App.orchestration.application.pipeline.pipeline_state_helpers import get_step_retries
    from backend.App.orchestration.domain.quality_gate_policy import should_retry as _qg_should_retry

    if not _quality_gate_env_default():
        return "continue"

    if step_id == "review_dev" and (
        state.get("dev_defect_report") is not None or state.get("dev_review_output") is not None
    ):
        return _dev_review_router(state)
    if step_id == "review_qa" and (
        state.get("qa_defect_report") is not None
        or state.get("qa_review_defect_report") is not None
        or state.get("qa_review_output") is not None
    ):
        return _qa_review_router(state)

    artifacts = state.get("step_artifacts") or {}
    artifact = artifacts.get(step_id) or {}
    verdict = str(artifact.get("verdict") or "").strip().upper()
    retries = get_step_retries(state, step_id)
    _max_retries = _max_step_retries_env()
    decision = _qg_should_retry(verdict, retries, _max_retries)

    if decision == "retry":
        logger.info(
            "QualityGate: step=%s verdict=NEEDS_WORK retries=%d/%d → retry",
            step_id, retries, _max_retries,
        )
    elif decision == "escalate":
        logger.info(
            "QualityGate: step=%s verdict=NEEDS_WORK retries=%d/%d → escalate",
            step_id, retries, _max_retries,
        )
    return decision


def _extract_verdict(text: str) -> str:
    return extract_verdict(text)


__all__ = [
    "ARTIFACT_AGENT_OUTPUT_KEYS",
    "PipelineState",
    "_with_approval_gate",
    "_dev_review_router",
    "_dev_retry_gate_node",
    "_qa_review_router",
    "_PipelineGraphBuilder",
    "_pipeline_should_cancel",
    "_code_analysis_is_weak",
    "_documentation_product_context_block",
    "_effective_spec_for_build",
    "_effective_spec_block_for_doc_chain",
    "_spec_arch_context_for_docs",
    "_remote_api_client_kwargs",
    "_remote_api_client_kwargs_for_role",
    "CodeDiagramAgent",
    "DocGenerateAgent",
    "ProblemSpotterAgent",
    "RefactorPlanAgent",
    "CustomSwarmRoleAgent",
    "clarify_input_node",
    "human_clarify_input_node",
    "pm_node",
    "review_pm_node",
    "human_pm_node",
    "ba_node",
    "review_ba_node",
    "human_ba_node",
    "arch_node",
    "review_stack_node",
    "review_arch_node",
    "human_arch_node",
    "ba_arch_debate_node",
    "merge_spec_node",
    "review_spec_node",
    "human_spec_node",
    "analyze_code_node",
    "generate_documentation_node",
    "problem_spotter_node",
    "refactor_plan_node",
    "human_code_review_node",
    "devops_node",
    "review_devops_node",
    "human_devops_node",
    "parse_dev_qa_task_plan",
    "read_dev_qa_task_count_target",
    "normalize_dev_qa_tasks_to_count",
    "dev_lead_node",
    "review_dev_lead_node",
    "human_dev_lead_node",
    "dev_node",
    "review_dev_node",
    "human_dev_node",
    "qa_node",
    "review_qa_node",
    "human_qa_node",
    "custom_role_step_id",
    "parse_custom_role_slug",
    "_make_custom_role_node",
    "PIPELINE_STEP_SEQUENCE",
    "PIPELINE_STEP_REGISTRY",
    "DEFAULT_PIPELINE_STEP_IDS",
    "PipelineStepRegistry",
    "_validate_pipeline_steps_from_registry",
    "_hook_wrap",
    "HUMAN_PIPELINE_STEP_TO_STATE_KEY",
    "_ASSEMBLED_USER_TASK_MARKER",
    "_COMPACTION_KEEP_KEYS",
    "_COMPACTION_SUMMARISE_KEYS",
    "_RUNTIME_STATE_KEYS",
    "_compact_state_if_needed",
    "_initial_pipeline_state",
    "_legacy_workspace_parts_from_input",
    "_migrate_legacy_pm_tasks_state",
    "_state_max_chars",
    "_state_snapshot",
    "format_human_resume_output",
    "human_pipeline_step_label",
    "_PipelineRunner",
    "_run_pipeline_impl",
    "_format_elapsed_wall",
    "_stream_progress_heartbeat_seconds",
    "final_pipeline_user_message",
    "primary_output_for_step",
    "task_store_agent_label",
    "run_pipeline_stream",
    "run_pipeline_stream_resume",
    "run_pipeline_stream_retry",
    "build_graph",
    "run_pipeline",
    "validate_pipeline_steps",
    "_resolve_pipeline_step",
    "_quality_gate_router",
    "_extract_verdict",
]


_GRAPH_STEP_IDS: frozenset[str] = frozenset({
    "pm", "review_pm", "human_pm",
    "ba", "review_ba", "human_ba",
    "architect", "review_stack", "review_arch", "human_arch",
    "spec_merge", "review_spec", "human_spec",
    "analyze_code", "generate_documentation",
    "problem_spotter", "refactor_plan", "human_code_review",
    "devops", "review_devops", "human_devops",
    "dev_lead", "review_dev_lead", "human_dev_lead",
    "dev", "review_dev", "human_dev",
    "qa", "review_qa", "human_qa",
})
_missing_in_registry = _GRAPH_STEP_IDS - set(PIPELINE_STEP_REGISTRY)
if _missing_in_registry:
    raise AssertionError(
        f"ARCH-12: build_graph() step ids not in PIPELINE_STEP_REGISTRY: {sorted(_missing_in_registry)}. "
        "Add them to PIPELINE_STEP_SEQUENCE or remove from build_graph()."
    )


def _resolve_pipeline_step(
    step_id: str,
    agent_config: Optional[dict[str, Any]],
) -> tuple[str, Callable[[PipelineState], dict[str, Any]]]:
    if step_id in PIPELINE_STEP_REGISTRY:
        label, node_func = PIPELINE_STEP_REGISTRY[step_id]
        return (label, _hook_wrap(step_id, node_func))
    role_slug = parse_custom_role_slug(step_id)
    if role_slug:
        custom_roles = (agent_config or {}).get("custom_roles")
        if isinstance(custom_roles, dict):
            role_config = custom_roles.get(role_slug)
            if isinstance(role_config, dict):
                title = str(role_config.get("title") or role_slug).strip()
                return (f"Custom: {title}", _hook_wrap(step_id, _make_custom_role_node(role_slug)))
    raise KeyError(step_id)


def validate_pipeline_steps(
    steps: list[str],
    agent_config: Optional[dict[str, Any]] = None,
) -> None:
    if not steps:
        raise ValueError("pipeline_steps must be a non-empty list")
    unknown: list[str] = []
    for step_id in steps:
        try:
            _resolve_pipeline_step(step_id, agent_config)
        except KeyError:
            unknown.append(step_id)
    if unknown:
        raise ValueError(f"Unknown pipeline step ids: {unknown}")


def validate_pipeline_stages(
    stages: list[list[str]],
    agent_config: Optional[dict[str, Any]] = None,
) -> None:
    if not stages:
        raise ValueError("pipeline_stages must be a non-empty list of stages")

    all_step_ids: list[str] = []
    seen: set[str] = set()

    for stage_idx, stage in enumerate(stages):
        if not isinstance(stage, list) or not stage:
            raise ValueError(
                f"pipeline_stages[{stage_idx}] must be a non-empty list of step IDs"
            )
        for step_id in stage:
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(
                    f"pipeline_stages[{stage_idx}] contains invalid step ID: {step_id!r}"
                )
            if step_id in seen:
                raise ValueError(
                    f"Duplicate step ID {step_id!r} in pipeline_stages"
                )
            seen.add(step_id)
            all_step_ids.append(step_id)

    if "clarify_input" in seen:
        if stages[0] != ["clarify_input"]:
            raise ValueError(
                "clarify_input must be the sole step in the first stage "
                "(cannot be parallelized or moved)"
            )

    validate_pipeline_steps(all_step_ids, agent_config)


def build_graph():
    return _PipelineGraphBuilder().build()


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
    return _run_pipeline_impl(
        user_input=user_input,
        agent_config=agent_config,
        pipeline_steps=pipeline_steps,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=pipeline_step_ids,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    output = run_pipeline("Landing page for a bakery")
    logger.info(output.get("qa_human_output", output.get("qa_output", "")))
