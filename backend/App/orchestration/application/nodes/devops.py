from __future__ import annotations

import logging
import os
from functools import lru_cache
from string import Template
from typing import Any

from backend.App.orchestration.infrastructure.agents.devops_agent import DevopsAgent
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.context.repo_evidence import (
    ensure_validated_repo_evidence,
    format_repo_evidence_for_prompt,
)
from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _dev_workspace_instructions,
    _documentation_locale_line,
    _effective_spec_for_build,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _should_use_mcp_for_workspace,
    _spec_for_build_mcp_safe,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    build_phase_pipeline_user_context,
    pipeline_user_task,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
)
from backend.App.shared.application.settings_resolver import get_setting_bool
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _prompt_fragments() -> dict[str, Any]:
    return load_app_config_json("prompt_fragments.json")


def _prompt_fragment(key: str) -> str:
    value = str(_prompt_fragments().get(key) or "")
    if not value:
        raise RuntimeError(f"prompt_fragments.{key} is empty")
    return value


def devops_node(state: PipelineState) -> dict[str, Any]:
    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        require_repo_path = get_setting_bool(
            "devops.require_repo_path",
            env_key="SWARM_DEVOPS_REQUIRE_REPO_PATH",
            default=True,
        )
        if require_repo_path:
            raise RuntimeError(
                "devops_node: workspace_root is not set; DevOps step requires a "
                "repository path so repo_evidence can be validated. Set "
                "agent_config.workspace_root or remove devops from the pipeline."
            )
        logger.warning(
            "devops_node: workspace_root is empty and "
            "SWARM_DEVOPS_REQUIRE_REPO_PATH=0 — continuing without repo evidence."
        )
    agent_config = state.get("agent_config") or {}
    cfg = agent_config.get("devops") or agent_config.get("dev") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    agent = DevopsAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_cfg_model(cfg),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    ws = _dev_workspace_instructions(state)
    ctx = _pipeline_context_block(state, "devops")
    _ca_raw = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    use_mcp = _should_use_mcp_for_workspace(state)
    ca_block = ""
    if not use_mcp and not _code_analysis_is_weak(code_analysis):
        ca_block = (
            _prompt_fragment("devops_existing_code_analysis_heading")
            + "\n"
            + _compact_code_analysis_for_prompt(code_analysis, max_chars=6000)
            + "\n\n"
        )
    prompt = Template(_prompt_fragment("devops_node_prompt_template")).safe_substitute(
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state, step_id="devops"),
        user_task=(
            pipeline_user_task(state)
            if use_mcp
            else build_phase_pipeline_user_context(state)
        ),
        spec=_spec_for_build_mcp_safe(state),
        code_analysis=ca_block,
    )
    prompt += ws
    devops_result, _, _ = _llm_planning_agent_run(agent, prompt, state)
    _devops_min = int(os.environ.get("SWARM_DEVOPS_MIN_OUTPUT_CHARS", "80"))
    if len((devops_result or "").strip()) < _devops_min:
        logger.warning(
            "devops_node: output too short (%d chars < %d min) — retrying once. task_id=%s",
            len((devops_result or "").strip()),
            _devops_min,
            str(state.get("task_id") or "")[:36],
        )
        retry_prompt = Template(
            _prompt_fragment("devops_retry_prompt_template")
        ).safe_substitute(base_prompt=prompt)
        devops_result, _, _ = _llm_planning_agent_run(agent, retry_prompt, state)
    devops_result, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=devops_result,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="devops_node",
        retry_run=lambda retry_prompt: _llm_planning_agent_run(agent, retry_prompt, state)[0],
    )
    if (devops_result or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "devops", devops_result)
    return {
        "devops_output": devops_result,
        "devops_model": agent.used_model,
        "devops_provider": agent.used_provider,
        "devops_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "devops_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
    }


def review_devops_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_devops_node")
    spec_full = _effective_spec_for_build(state)
    spec_art = embedded_review_artifact(
        state,
        spec_full,
        log_node="review_devops_node",
        part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS",
        default_max=40_000,
        mcp_max=3_000,
    )
    devops_art = embedded_review_artifact(
        state,
        state.get("devops_output"),
        log_node="review_devops_node",
        part_name="devops_output",
        env_name="SWARM_REVIEW_DEVOPS_OUTPUT_MAX_CHARS",
        default_max=60_000,
        mcp_max=4_000,
    )
    repo_evidence_artifact = {
        "repo_evidence": list(state.get("devops_repo_evidence") or []),
        "unverified_claims": list(state.get("devops_unverified_claims") or []),
    }
    execution_contract = state.get("devops_execution_contract")
    execution_contract_text = (
        str(execution_contract)
        if isinstance(execution_contract, dict)
        else "{}"
    )
    prompt = Template(_prompt_fragment("review_devops_prompt_template")).safe_substitute(
        user_task=user_block,
        spec=spec_art,
        repo_evidence=format_repo_evidence_for_prompt(repo_evidence_artifact),
        execution_contract=execution_contract_text,
        devops_output=devops_art,
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_devops",
        prompt=prompt,
        output_key="devops_review_output",
        model_key="devops_review_model",
        provider_key="devops_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_devops_node(state: PipelineState) -> dict[str, Any]:
    bundle = Template(_prompt_fragment("human_devops_bundle_template")).safe_substitute(
        devops_output=state["devops_output"],
        review_output=state["devops_review_output"],
    )
    agent = _make_human_agent(state, "devops")
    return {"devops_human_output": agent.run(bundle)}
