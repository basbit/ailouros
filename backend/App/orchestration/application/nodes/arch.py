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
from backend.App.orchestration.application.nodes._prompt_builders import _run_agent_with_boundary

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
        planning_retry_block = (
            "Reviewer feedback from previous Architect/stack review attempt "
            "(fix all issues before returning a new architecture artifact):\n"
            f"{planning_retry_feedback[:4000]}\n\n"
        )
    prompt = (
        ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state, step_id="architect")
        + "User task:\n"
        + f"{plan_ctx}\n\n"
        + "PM decomposition:\n"
        + f"{pm_output}\n\n"
        + planning_retry_block
        + "Evidence contract:\n"
        + "If you claim that the repository already uses a technology, pattern, component, "
        + "or integration, add a final ```json``` block with this schema:\n"
        + '{'
        + '"repo_evidence":[{"path":"relative/path","start_line":1,"end_line":3,'
        + '"excerpt":"exact text copied from the repository","why":"what this proves"}],'
        + '"unverified_claims":["claim that cannot be proven from the repository yet"]'
        + '}\n'
        + "Rules:\n"
        + "- `excerpt` must exactly match the referenced file lines.\n"
        + "- Every tech-stack or existing-system claim must be backed by `repo_evidence` or moved to "
        + "`unverified_claims`.\n"
        + "- Do not omit the JSON block.\n"
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
    prompt = (
        _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + "Step: technology stack approval (after Architect).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Technology claims about the existing repository are backed by validated repo_evidence\n"
        "[ ] Any unproven stack claim is explicitly listed under unverified_claims\n"
        "[ ] The proposed stack does not rely on invented repository facts\n\n"
        f"User task:\n{user_block}\n\n"
        "Validated repo evidence artifact:\n"
        f"{format_repo_evidence_for_prompt(repo_evidence_artifact)}\n\n"
        "Architect artifact (expected: Technology stack / ADR):\n"
        f"{arch_art}"
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
    prompt = (
        _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + "Step: architect (general artifact review, not just the stack).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Explicit Technology Stack section is present (languages, frameworks, DB, deployment)\n"
        "[ ] Existing-repository claims are backed by validated repo_evidence or marked unverified\n"
        "[ ] ADR or justification provided for non-obvious decisions\n"
        "[ ] Component/service boundaries are defined\n"
        "[ ] No requirements from BA are silently dropped\n"
        "[ ] Scalability/security decisions match the task scope (no over/under-engineering)\n\n"
        f"User task:\n{user_block}\n\n"
        "Validated repo evidence artifact:\n"
        f"{format_repo_evidence_for_prompt(repo_evidence_artifact)}\n\n"
        "Stack review (separate reviewer, for context):\n"
        f"{stack_rev}\n\n"
        f"Architect artifact:\n{arch_art}"
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
    prompt = (
        "You are the Judge in an architectural debate (DebateWithJudge).\n"
        "You are given:\n"
        "1) Requirements from the Business Analyst (WHAT is needed)\n"
        "2) Architectural decisions from the Architect (HOW to implement)\n\n"
        "Task: find conflicts/inconsistencies and propose a compromise.\n\n"
        "=== BA Requirements ===\n"
        f"{ba}\n\n"
        "=== Architect Decisions ===\n"
        f"{arch}\n\n"
        "Respond in the following format:\n"
        "## Identified conflicts\n"
        "1. [conflict]: [resolution proposal]\n"
        "…\n\n"
        "## Specification clarifications\n"
        "[final clarifications that resolve the conflicts]"
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
        merge_prompt = (
            "Merge the following two specification sections into ONE coherent, "
            "non-redundant specification document.\n"
            "Remove duplicates. Preserve ALL unique requirements and architectural decisions.\n"
            "Keep structured sections (tables, ADRs, code blocks) intact.\n\n"
            f"=== BA Requirements ===\n{ba_out}\n\n"
            f"=== Architecture Decisions ===\n{arch_out}\n\n"
        )
        if debate:
            merge_prompt += f"=== Debate Outcome ===\n{debate}\n\n"
        merge_prompt += "Output the merged specification. Do NOT add commentary."
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
    prompt = (
        "Step: spec_merge (merged specification before Dev).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Technology stack is explicitly named (no 'TBD' or 'as needed')\n"
        "[ ] BA requirements and Architect decisions are consistent (no contradictions)\n"
        "[ ] All user task goals are traceable to at least one requirement\n"
        "[ ] Spec is self-contained enough for Dev/QA to proceed without re-reading the chat\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}"
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
