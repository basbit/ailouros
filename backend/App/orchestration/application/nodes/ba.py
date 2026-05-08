from __future__ import annotations

from collections.abc import Mapping
from string import Template
from typing import Any

from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
)

from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
from backend.App.integrations.infrastructure.cross_task_memory import format_cross_task_memory_block
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.context.source_research import (
    ensure_source_research,
)
from backend.App.orchestration.application.context.repo_evidence import (
    ensure_validated_repo_evidence,
    format_repo_evidence_for_prompt,
)
from backend.App.orchestration.application.nodes._shared import (
    _artifact_memory_lines,
    _cfg_model,
    _documentation_locale_line,
    _web_research_guidance_block,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _repo_memory_facts,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    planning_pipeline_user_context,
)


def _ba_memory_artifact(ba_output: str, repo_evidence_artifact: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        "verified_facts": _repo_memory_facts(list(repo_evidence_artifact.get("repo_evidence") or [])),
        "hypotheses": [],
        "decisions": _artifact_memory_lines(ba_output, max_items=4),
        "dead_ends": [],
        "constraints": list(repo_evidence_artifact.get("unverified_claims") or [])[:4],
    }


def ba_node(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.context.context_budget import get_context_budget

    ensure_source_research(state, caller_step="ba")
    plan_ctx = planning_pipeline_user_context(state)
    _budget = get_context_budget("ba", state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None)
    xmem = format_cross_task_memory_block(
        state, plan_ctx, current_step="ba", max_chars=_budget.cross_task_memory_chars,
    )
    ctx = _pipeline_context_block(state, "ba")
    pm_output = state.get("pm_output") or ""
    prompt = Template(_prompt_fragment("ba_node_prompt_template")).safe_substitute(
        xmem=xmem,
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        research_guidance=_web_research_guidance_block(state, role="ba"),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state, step_id="ba"),
        plan_ctx=plan_ctx,
        pm_output=pm_output,
    )
    ba_cfg = (state.get("agent_config") or {}).get("ba") or {}
    agent = BAAgent(
        system_prompt_path_override=ba_cfg.get("prompt_path") or ba_cfg.get("prompt"),
        model_override=_cfg_model(ba_cfg),
        environment_override=ba_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, ba_cfg),
        **_remote_api_client_kwargs_for_role(state, ba_cfg),
    )
    ba_output, _, _ = _llm_planning_agent_run(agent, prompt, state)
    ba_output, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=ba_output,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="ba_node",
        retry_run=lambda retry_prompt: _llm_planning_agent_run(agent, retry_prompt, state)[0],
    )
    if (ba_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ba", ba_output)
    return {
        "ba_output": ba_output,
        "ba_model": agent.used_model,
        "ba_provider": agent.used_provider,
        "ba_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "ba_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
        "ba_memory_artifact": _ba_memory_artifact(ba_output, validated_repo_evidence),
    }


def review_ba_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_ba_node")
    ba_art = embedded_review_artifact(
        state,
        state.get("ba_output"),
        log_node="review_ba_node",
        part_name="ba_output",
        env_name="SWARM_REVIEW_BA_OUTPUT_MAX_CHARS",
        default_max=60_000,
    )
    repo_evidence_artifact = {
        "repo_evidence": list(state.get("ba_repo_evidence") or []),
        "unverified_claims": list(state.get("ba_unverified_claims") or []),
    }
    prompt = Template(_prompt_fragment("review_ba_prompt_template")).safe_substitute(
        user_block=user_block,
        repo_evidence=format_repo_evidence_for_prompt(repo_evidence_artifact),
        ba_artifact=ba_art,
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_ba",
        prompt=prompt,
        output_key="ba_review_output",
        model_key="ba_review_model",
        provider_key="ba_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_ba_node(state: PipelineState) -> dict[str, Any]:
    bundle = f"BA:\n{state['ba_output']}\n\nReview:\n{state['ba_review_output']}"
    agent = _make_human_agent(state, "ba")
    return {"ba_human_output": agent.run(bundle)}
