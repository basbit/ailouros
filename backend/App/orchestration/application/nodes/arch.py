from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from backend.App.orchestration.infrastructure.agents.arch_agent import ArchitectAgent
from backend.App.orchestration.infrastructure.agents.stack_reviewer_agent import StackReviewerAgent
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
    _stack_reviewer_cfg,
    _swarm_prompt_prefix,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    planning_pipeline_user_context,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
    _run_agent_with_boundary,
)
from string import Template

_log = logging.getLogger(__name__)


def _arch_memory_artifact(arch_output: str, repo_evidence_artifact: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        "verified_facts": _repo_memory_facts(list(repo_evidence_artifact.get("repo_evidence") or [])),
        "hypotheses": [],
        "decisions": _artifact_memory_lines(arch_output, max_items=4),
        "dead_ends": [],
        "constraints": list(repo_evidence_artifact.get("unverified_claims") or [])[:4],
    }


def _spec_memory_artifact(state: PipelineState, spec_output: str) -> dict[str, list[str]]:
    ba_claims = list(state.get("ba_unverified_claims") or [])
    arch_claims = list(state.get("arch_unverified_claims") or [])
    constraints: list[str] = []
    for claim in ba_claims + arch_claims:
        text = str(claim or "").strip()
        if text and text not in constraints:
            constraints.append(text)
    decisions = ["Approved specification merged from BA and Architect artifacts."]
    if (state.get("ba_arch_debate_output") or "").strip():
        decisions.append("BA/Architect conflict-resolution notes were incorporated into the merged spec.")
    decisions.extend(_artifact_memory_lines(spec_output, max_items=2))
    facts = _repo_memory_facts(list(state.get("ba_repo_evidence") or []))
    for item in _repo_memory_facts(list(state.get("arch_repo_evidence") or [])):
        if item not in facts:
            facts.append(item)
    return {
        "verified_facts": facts[:6],
        "hypotheses": [],
        "decisions": decisions[:4],
        "dead_ends": [],
        "constraints": constraints[:4],
    }


def arch_node(state: PipelineState) -> dict[str, Any]:
    ensure_source_research(state, caller_step="architect")
    plan_ctx = planning_pipeline_user_context(state)
    ctx = _pipeline_context_block(state, "architect")
    pm_output = state.get("pm_output") or ""
    planning_retry_feedback = str((state.get("planning_review_feedback") or {}).get("architect") or "").strip()
    planning_retry_block = ""
    if planning_retry_feedback:
        planning_retry_block = Template(
            _prompt_fragment("arch_planning_retry_block_template")
        ).safe_substitute(feedback=planning_retry_feedback[:4000])
    prompt = Template(_prompt_fragment("arch_node_prompt_template")).safe_substitute(
        context=ctx,
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        research_guidance=_web_research_guidance_block(state, role="architect"),
        mcp_instruction=planning_mcp_tool_instruction(state),
        knowledge=_project_knowledge_block(state, step_id="architect"),
        plan_ctx=plan_ctx,
        pm_output=pm_output,
        retry_block=planning_retry_block,
    )
    architect_cfg = (state.get("agent_config") or {}).get("architect") or {}
    agent = ArchitectAgent(
        system_prompt_path_override=architect_cfg.get("prompt_path") or architect_cfg.get("prompt"),
        model_override=_cfg_model(architect_cfg),
        environment_override=architect_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, architect_cfg),
        **_remote_api_client_kwargs_for_role(state, architect_cfg),
    )
    arch_output, _, _ = _llm_planning_agent_run(agent, prompt, state)
    arch_output, validated_repo_evidence = ensure_validated_repo_evidence(
        raw_output=arch_output,
        base_prompt=prompt,
        workspace_root=str(state.get("workspace_root") or ""),
        step_id="arch_node",
        retry_run=lambda retry_prompt: _llm_planning_agent_run(agent, retry_prompt, state)[0],
    )
    _arch_min = int(os.environ.get("SWARM_ARCH_MIN_OUTPUT_CHARS", "150"))
    if len((arch_output or "").strip()) < _arch_min:
        import logging as _log_mod
        _log_mod.getLogger(__name__).error(
            "Architect produced insufficient output (%d chars < %d min). "
            "Model may lack capability for architecture design. task_id=%s",
            len((arch_output or "").strip()), _arch_min,
            (state.get("task_id") or "")[:36],
        )
        arch_output = (
            f"[ARCH ERROR] Architect output too short ({len((arch_output or '').strip())} chars, "
            f"min {_arch_min}). Model could not produce an architecture. "
            f"Original output:\n{(arch_output or '').strip()}"
        )
    if (arch_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "architect", arch_output)
    return {
        "arch_output": arch_output,
        "arch_model": agent.used_model,
        "arch_provider": agent.used_provider,
        "arch_repo_evidence": validated_repo_evidence.get("repo_evidence") or [],
        "arch_unverified_claims": validated_repo_evidence.get("unverified_claims") or [],
        "arch_memory_artifact": _arch_memory_artifact(arch_output, validated_repo_evidence),
    }


def review_stack_node(state: PipelineState) -> dict[str, Any]:
    stack_reviewer_config = _stack_reviewer_cfg(state)

    def _stack_factory() -> StackReviewerAgent:
        return StackReviewerAgent(
            system_prompt_path_override=stack_reviewer_config.get("prompt_path") or stack_reviewer_config.get("prompt"),
            model_override=stack_reviewer_config.get("model"),
            environment_override=stack_reviewer_config.get("environment"),
            system_prompt_extra=_skills_extra_for_role_cfg(state, stack_reviewer_config),
            **_remote_api_client_kwargs_for_role(state, stack_reviewer_config),
        )

    user_block = embedded_pipeline_input_for_review(state, log_node="review_stack_node")
    arch_art = embedded_review_artifact(
        state,
        state.get("arch_output"),
        log_node="review_stack_node",
        part_name="arch_output",
        env_name="SWARM_REVIEW_ARCH_ARTIFACT_MAX_CHARS",
        default_max=60_000,
    )
    repo_evidence_artifact = {
        "repo_evidence": list(state.get("arch_repo_evidence") or []),
        "unverified_claims": list(state.get("arch_unverified_claims") or []),
    }
    prompt = Template(_prompt_fragment("review_stack_prompt_template")).safe_substitute(
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        user_block=user_block,
        repo_evidence=format_repo_evidence_for_prompt(repo_evidence_artifact),
        arch_artifact=arch_art,
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_stack",
        prompt=prompt,
        output_key="stack_review_output",
        model_key="stack_review_model",
        provider_key="stack_review_provider",
        agent_factory=_stack_factory,
    )


def review_arch_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_arch_node")
    stack_rev = embedded_review_artifact(
        state,
        state.get("stack_review_output"),
        log_node="review_arch_node",
        part_name="stack_review_output",
        env_name="SWARM_REVIEW_STACK_REVIEW_MAX_CHARS",
        default_max=32_000,
    )
    arch_art = embedded_review_artifact(
        state,
        state.get("arch_output"),
        log_node="review_arch_node",
        part_name="arch_output",
        env_name="SWARM_REVIEW_ARCH_ARTIFACT_MAX_CHARS",
        default_max=60_000,
    )
    repo_evidence_artifact = {
        "repo_evidence": list(state.get("arch_repo_evidence") or []),
        "unverified_claims": list(state.get("arch_unverified_claims") or []),
    }
    prompt = Template(_prompt_fragment("review_arch_prompt_template")).safe_substitute(
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        user_block=user_block,
        repo_evidence=format_repo_evidence_for_prompt(repo_evidence_artifact),
        stack_review=stack_rev,
        arch_artifact=arch_art,
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_arch",
        prompt=prompt,
        output_key="arch_review_output",
        model_key="arch_review_model",
        provider_key="arch_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_arch_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        "Architect:\n"
        f"{state['arch_output']}\n\n"
        "Stack review:\n"
        f"{state['stack_review_output']}\n\n"
        "General Architect review:\n"
        f"{state['arch_review_output']}"
    )
    agent = _make_human_agent(state, "arch")
    return {"arch_human_output": agent.run(bundle)}


def ba_arch_debate_node(state: PipelineState) -> dict[str, Any]:
    ba = (state.get("ba_output") or "").strip()
    arch = (state.get("arch_output") or "").strip()
    if not ba or not arch:
        import logging as _lg
        _lg.getLogger(__name__).info(
            "ba_arch_debate: skipped (ba=%d chars, arch=%d chars) — "
            "both outputs required for debate",
            len(ba), len(arch),
        )
        return {
            "ba_arch_debate_output": "",
            "ba_arch_debate_model": "",
            "ba_arch_debate_provider": "",
        }
    prompt = Template(_prompt_fragment("ba_arch_debate_prompt_template")).safe_substitute(
        ba=ba,
        arch=arch,
    )
    agent = _make_reviewer_agent(state)
    debate_output = _run_agent_with_boundary(state, agent, prompt)
    if not (debate_output or "").strip():
        _log.warning(
            "ba_arch_debate_node: model returned empty output — retrying. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        debate_output = _run_agent_with_boundary(state, agent, prompt)
    if not (debate_output or "").strip():
        _log.error(
            "ba_arch_debate_node: model returned empty output after retry. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
    if (debate_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "ba_arch_debate", debate_output)
    return {
        "ba_arch_debate_output": debate_output,
        "ba_arch_debate_model": agent.used_model,
        "ba_arch_debate_provider": agent.used_provider,
    }


_MERGE_SPEC_MIN_INPUT_CHARS = int(os.environ.get("SWARM_MERGE_SPEC_MIN_INPUT_CHARS", "200"))


def _concat_spec_parts(ba_out: str, arch_out: str, debate: str) -> str:
    parts = [
        "BA specification section:\n" + ba_out,
        "Architect specification section:\n" + arch_out,
    ]
    if debate:
        parts.append("BA\u2194Architect debate outcome (DebateWithJudge):\n" + debate)
    return "# Approved Specification\n\n" + "\n\n".join(parts)


def merge_spec_node(state: PipelineState) -> dict[str, Any]:
    ba_out = (state.get("ba_output") or "").strip()
    arch_out = (state.get("arch_output") or "").strip()

    if len(ba_out) < _MERGE_SPEC_MIN_INPUT_CHARS and len(arch_out) < _MERGE_SPEC_MIN_INPUT_CHARS:
        import logging as _logging
        _logging.getLogger(__name__).error(
            "spec_merge: both ba_output (%d chars) and arch_output (%d chars) are below "
            "minimum threshold (%d chars). Upstream agents likely failed. "
            "Skipping spec_merge to avoid producing an empty specification.",
            len(ba_out), len(arch_out), _MERGE_SPEC_MIN_INPUT_CHARS,
        )
        return {
            "spec_output": (
                "# Specification (degraded)\n\n"
                "WARNING: Both BA and Architect outputs are empty or too short "
                f"(ba={len(ba_out)} chars, arch={len(arch_out)} chars). "
                "Pipeline continues with degraded spec — dev/qa steps may produce lower quality output."
            ),
        }

    debate = (state.get("ba_arch_debate_output") or "").strip()

    combined_size = len(ba_out) + len(arch_out) + len(debate)
    _llm_merge_threshold = int(os.environ.get("SWARM_SPEC_MERGE_LLM_THRESHOLD", "8000"))
    if combined_size > _llm_merge_threshold:
        debate_block = ""
        if debate:
            debate_block = Template(
                _prompt_fragment("merge_spec_debate_block_template")
            ).safe_substitute(debate=debate)
        merge_prompt = Template(
            _prompt_fragment("merge_spec_prompt_template")
        ).safe_substitute(
            ba=ba_out,
            arch=arch_out,
            debate_block=debate_block,
        )
        try:
            agent = _make_reviewer_agent(state)
            merged = _run_agent_with_boundary(state, agent, merge_prompt)
            if merged and len(merged.strip()) > _MERGE_SPEC_MIN_INPUT_CHARS:
                spec_output = "# Approved Specification\n\n" + merged
            else:
                import logging as _merge_log
                _merge_log.getLogger(__name__).warning(
                    "spec_merge: LLM merge returned short output (%d chars), "
                    "falling back to concatenation", len(merged or ""),
                )
                spec_output = _concat_spec_parts(ba_out, arch_out, debate)
        except Exception as exc:
            import logging as _merge_log
            _merge_log.getLogger(__name__).warning(
                "spec_merge: LLM merge failed (%s), falling back to concatenation", exc,
            )
            spec_output = _concat_spec_parts(ba_out, arch_out, debate)
    else:
        spec_output = _concat_spec_parts(ba_out, arch_out, debate)
    if spec_output.strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "spec_merge", spec_output)
    return {
        "spec_output": spec_output,
        "spec_memory_artifact": _spec_memory_artifact(state, spec_output),
    }


def review_spec_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_spec_node")
    spec_art = embedded_review_artifact(
        state,
        state.get("spec_output"),
        log_node="review_spec_node",
        part_name="spec_output",
        env_name="SWARM_REVIEW_MERGED_SPEC_MAX_CHARS",
        default_max=80_000,
    )
    prompt = Template(_prompt_fragment("review_spec_prompt_template")).safe_substitute(
        user_block=user_block,
        spec_artifact=spec_art,
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_spec",
        prompt=prompt,
        output_key="spec_review_output",
        model_key="spec_review_model",
        provider_key="spec_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_spec_node(state: PipelineState) -> dict[str, Any]:
    bundle = f"Spec:\n{state['spec_output']}\n\nReview:\n{state['spec_review_output']}"
    agent = _make_human_agent(state, "spec")
    return {"spec_human_output": agent.run(bundle)}
