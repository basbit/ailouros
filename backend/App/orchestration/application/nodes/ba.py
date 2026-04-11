"""BA pipeline nodes: ba, review_ba, human_ba."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.App.orchestration.infrastructure.agents.ba_agent import BAAgent
from backend.App.integrations.infrastructure.cross_task_memory import format_cross_task_memory_block
from backend.App.orchestration.application.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.repo_evidence import (
    ensure_validated_repo_evidence,
    format_repo_evidence_for_prompt,
)
from backend.App.orchestration.application.nodes._shared import (
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    planning_pipeline_user_context,
)


def _repo_memory_facts(repo_evidence: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    for item in repo_evidence:
        path = str(item.get("path") or "").strip()
        why = str(item.get("why") or "").strip()
        if not why:
            continue
        text = why if not path else f"{why} ({path})"
        if text not in facts:
            facts.append(text)
    return facts[:6]


def _artifact_memory_lines(raw: str, *, max_items: int = 4, max_chars: int = 180) -> list[str]:
    items: list[str] = []
    for line in (raw or "").splitlines():
        text = line.strip().lstrip("-*# ").strip()
        if not text or text.startswith("```") or text.startswith("{") or text in items:
            continue
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _ba_memory_artifact(ba_output: str, repo_evidence_artifact: Mapping[str, Any]) -> dict[str, list[str]]:
    return {
        "verified_facts": _repo_memory_facts(list(repo_evidence_artifact.get("repo_evidence") or [])),
        "hypotheses": [],
        "decisions": _artifact_memory_lines(ba_output, max_items=4),
        "dead_ends": [],
        "constraints": list(repo_evidence_artifact.get("unverified_claims") or [])[:4],
    }


def ba_node(state: PipelineState) -> dict[str, Any]:
    plan_ctx = planning_pipeline_user_context(state)
    xmem = format_cross_task_memory_block(
        state, plan_ctx, current_step="ba"
    )
    ctx = _pipeline_context_block(state, "ba")
    pm_output = state.get("pm_output") or ""
    prompt = (
        xmem
        + ctx
        + _swarm_prompt_prefix(state)
        + planning_mcp_tool_instruction(state)
        + _project_knowledge_block(state)
        + "User task:\n"
        f"{plan_ctx}\n\n"
        "PM decomposition:\n"
        f"{pm_output}\n\n"
        "Evidence contract:\n"
        "If you claim that the current repository or existing product already contains a module, "
        "entity, workflow, endpoint, or business constraint, add a final ```json``` block with:\n"
        '{'
        '"repo_evidence":[{"path":"relative/path","start_line":1,"end_line":3,'
        '"excerpt":"exact text copied from the repository","why":"what existing product fact this proves"}],'
        '"unverified_claims":["existing-system claim that cannot be proven from the repository yet"]'
        '}\n'
        "Rules:\n"
        "- Only repo-based factual claims go into this artifact.\n"
        "- If a claim cannot be proven from the repository, put it into `unverified_claims`.\n"
        "- Do not omit the JSON block.\n"
    )
    ba_cfg = (state.get("agent_config") or {}).get("ba") or {}
    agent = BAAgent(
        system_prompt_path_override=ba_cfg.get("prompt_path") or ba_cfg.get("prompt"),
        model_override=ba_cfg.get("model"),
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
        from backend.App.workspace.application.doc_workspace import write_pipeline_step_to_workspace
        write_pipeline_step_to_workspace(state, "ba", ba_output)
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
    prompt = (
        "Step: ba (Business Analyst).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] No stack/technology decisions made (stack belongs to Architect)\n"
        "[ ] Existing-product or repository claims are backed by validated repo_evidence or explicitly marked unverified\n"
        "[ ] User stories have explicit acceptance criteria (Given/When/Then or equivalent)\n"
        "[ ] No full PRD produced for a small (XS/S) scope — output must match scope size\n"
        "[ ] All PM tasks are covered in BA requirements\n"
        "[ ] No requirements directly contradict the user task\n\n"
        f"User task:\n{user_block}\n\n"
        "Validated repo evidence artifact:\n"
        f"{format_repo_evidence_for_prompt(repo_evidence_artifact)}\n\n"
        f"BA artifact:\n{ba_art}"
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
