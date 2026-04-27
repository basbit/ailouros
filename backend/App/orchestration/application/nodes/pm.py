from __future__ import annotations

import logging
import os
from typing import Any

from backend.App.orchestration.infrastructure.agents.pm_agent import PMAgent
from backend.App.integrations.infrastructure.cross_task_memory import format_cross_task_memory_block
from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.ephemeral_state import set_ephemeral
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.context.source_research import (
    ensure_source_research,
)
from backend.App.orchestration.application.nodes._shared import (
    _documentation_locale_line,
    _env_model_override,
    _llm_planning_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    planning_mcp_tool_instruction,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _stream_automation_emit,
    _swarm_prompt_prefix,
    _web_research_guidance_block,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    planning_pipeline_user_context,
)
from backend.App.orchestration.application.nodes.pm_clarify import (
    clarify_input_node,
    human_clarify_input_node,
)
from backend.App.orchestration.infrastructure.agents.reviewer_agent import ReviewerAgent

__all__ = [
    "pm_node",
    "review_pm_node",
    "human_pm_node",
    "clarify_input_node",
    "human_clarify_input_node",
    "ReviewerAgent",
]

_log = logging.getLogger(__name__)


def _compact_memory_lines(raw: str, *, max_items: int = 6, max_chars: int = 180) -> list[str]:
    """PM-style memory-line compaction (strips numeric list prefixes).

    Delegates to :class:`shared.application.memory_artifacts.MemoryArtifactBuilder`
    with PM-specific presets (``1. foo`` → ``foo``, keep code fences).
    """
    from backend.App.shared.application.memory_artifacts import MemoryArtifactBuilder

    return MemoryArtifactBuilder(
        max_items=max_items,
        max_chars=max_chars,
        strip_numeric_list_prefix=True,
        drop_code_fences=False,
        drop_json_like=False,
    ).compact(raw)


def _pm_memory_artifact(plan_ctx: str, pm_output: str) -> dict[str, list[str]]:
    decisions = _compact_memory_lines(pm_output, max_items=6, max_chars=180)
    constraints = _compact_memory_lines(plan_ctx, max_items=3, max_chars=180)
    return {
        "facts": [],
        "hypotheses": [],
        "decisions": decisions,
        "dead_ends": [],
        "constraints": constraints,
    }


def _collect_pm_evidence_packet(workspace_root: str, task_text: str, *, max_chars: int = 3000) -> str:
    if os.getenv("SWARM_PM_EVIDENCE_PREFETCH", "1").strip() not in ("1", "true", "yes", "on"):
        _log.info("PM evidence prefetch DISABLED (SWARM_PM_EVIDENCE_PREFETCH != 1) — PM will use only inline context")
        return ""

    ws = workspace_root.strip() if workspace_root else ""
    if not ws or not os.path.isdir(ws):
        return ""

    parts: list[str] = []
    budget = max_chars

    manifest_candidates = [
        "composer.json", "package.json", "pyproject.toml", "setup.py",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    ]
    for fname in manifest_candidates:
        fpath = os.path.join(ws, fname)
        if os.path.isfile(fpath):
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read()
                snippet = content[:800]
                parts.append(f"# {fname}\n```\n{snippet}\n```")
                budget -= len(snippet) + 30
            except OSError:
                pass
            if budget <= 0:
                break

    if budget > 200:
        doc_candidates = [
            "README.md", "readme.md", "README.rst",
            "ARCHITECTURE.md", "ARCHITECTURE.rst", "architecture.md",
            "CONTRIBUTING.md", "DESIGN.md", "OVERVIEW.md",
        ]
        for fname in doc_candidates:
            fpath = os.path.join(ws, fname)
            if os.path.isfile(fpath):
                try:
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    snippet = content[:600]
                    parts.append(f"# {fname}\n{snippet}")
                    budget -= len(snippet) + 20
                except OSError:
                    pass
                if budget <= 0:
                    break
        docs_dir = os.path.join(ws, "docs")
        if budget > 100 and os.path.isdir(docs_dir):
            try:
                for fname in sorted(os.listdir(docs_dir))[:4]:
                    if not fname.endswith((".md", ".rst", ".txt")):
                        continue
                    fpath = os.path.join(docs_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    content = open(fpath, encoding="utf-8", errors="replace").read()
                    snippet = content[:400]
                    parts.append(f"# docs/{fname}\n{snippet}")
                    budget -= len(snippet) + 20
                    if budget <= 0:
                        break
            except OSError:
                pass

    if budget > 300:
        tree_lines: list[str] = []
        _ignored = {"node_modules", ".git", "__pycache__", ".venv", "venv", "vendor",
                    "dist", "build", ".next", ".nuxt", "coverage", ".mypy_cache"}
        try:
            for root, dirs, files in os.walk(ws):
                dirs[:] = [d for d in dirs if d not in _ignored and not d.startswith(".")]
                rel = os.path.relpath(root, ws)
                depth = 0 if rel == "." else rel.count(os.sep) + 1
                if depth > 3:
                    dirs.clear()
                    continue
                indent = "  " * depth
                dir_name = os.path.basename(root) if rel != "." else ws
                tree_lines.append(f"{indent}{dir_name}/")
                for f in sorted(files)[:20]:
                    tree_lines.append(f"{indent}  {f}")
                if len(tree_lines) > 120:
                    tree_lines.append("  ... (truncated)")
                    dirs.clear()
        except OSError:
            pass
        if tree_lines:
            tree_block = "\n".join(tree_lines[:120])
            parts.append(f"# Workspace tree\n```\n{tree_block}\n```")
            budget -= len(tree_block) + 30

    if budget > 200:
        task_words = set(
            w.lower() for w in task_text.replace("_", " ").split()
            if len(w) > 3 and w.isalpha()
        )
        _src_exts = {
            ".php", ".py", ".ts", ".js", ".go", ".rb", ".java", ".cs", ".rs",
            ".gd", ".gdscript", ".lua", ".cpp", ".hpp", ".h", ".c",
            ".swift", ".kt", ".dart",
        }
        _ignored_src = {"node_modules", ".git", "__pycache__", ".venv", "venv", "vendor",
                        "dist", "build", ".next", ".nuxt", "coverage", ".mypy_cache"}
        relevant_paths: list[str] = []
        try:
            for root, dirs, files in os.walk(ws):
                dirs[:] = [d for d in dirs if d not in _ignored_src and not d.startswith(".")]
                rel_root = os.path.relpath(root, ws)
                if rel_root.count(os.sep) > 4:
                    dirs.clear()
                    continue
                for f in files:
                    if not any(f.endswith(ext) for ext in _src_exts):
                        continue
                    fname_lower = f.lower().replace("_", " ").replace("-", " ")
                    if any(w in fname_lower for w in task_words):
                        relevant_paths.append(os.path.join(root, f))
                    if len(relevant_paths) >= 10:
                        break
                if len(relevant_paths) >= 10:
                    break
        except OSError:
            pass

        shown = 0
        for fpath in relevant_paths[:5]:
            if budget <= 100:
                break
            try:
                content = open(fpath, encoding="utf-8", errors="replace").read()
                rel_fpath = os.path.relpath(fpath, ws)
                snippet = content[:min(400, budget - 80)]
                parts.append(f"# {rel_fpath}\n```\n{snippet}\n```")
                budget -= len(snippet) + 50
                shown += 1
            except OSError:
                pass
        if shown:
            _log.debug("PM evidence prefetch: injected %d relevant files", shown)

    if not parts:
        _log.info("PM evidence prefetch: no evidence collected (empty workspace or no matching files)")
        return ""

    _log.info(
        "PM evidence prefetch: injected %d block(s) (~%d chars total) into PM prompt",
        len(parts),
        sum(len(p) for p in parts),
    )
    return (
        "\n[Repository evidence — deterministically prefetched before PM]\n"
        + "\n\n".join(parts)
        + "\n\n"
    )


def pm_node(state: PipelineState) -> dict[str, Any]:
    from backend.App.orchestration.application.context.context_budget import get_context_budget

    ensure_source_research(state, caller_step="pm")
    plan_ctx = planning_pipeline_user_context(state)
    _budget = get_context_budget("pm", state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None)
    mem = format_pattern_memory_block(state, plan_ctx, max_chars=_budget.pattern_memory_chars)
    xmem = format_cross_task_memory_block(
        state, plan_ctx, current_step="pm", max_chars=_budget.cross_task_memory_chars,
    )
    ctx = _pipeline_context_block(state, "pm")
    clarify_human = (state.get("clarify_input_human_output") or "").strip()
    _no_clarify = (
        not clarify_human
        or clarify_human.startswith("[human:clarify_input] Input confirmed ready")
        or clarify_human.startswith("[human:clarify_input] APPROVED (auto)")
        or clarify_human.startswith("[human:clarify_input] Confirmed manually")
    )
    if _no_clarify:
        raw_input = plan_ctx
    else:
        raw_input = (
            plan_ctx
            + "\n\n[User clarifications (answers to pre-pipeline questions)]\n"
            + clarify_human
        )
    _workspace_root = str(state.get("workspace_root") or "").strip()
    _task_text = str(state.get("input") or plan_ctx or "")
    _evidence_block = _collect_pm_evidence_packet(_workspace_root, _task_text)
    if _evidence_block and not state.get("workspace_evidence_brief"):
        set_ephemeral(state, "workspace_evidence_brief", _evidence_block)
    _ca_block = ""
    _analyze_out = (state.get("analyze_code_output") or "").strip()
    if _analyze_out:
        _ca_block = (
            "\n[Repository code analysis — use this to determine the actual tech stack]\n"
            + _analyze_out[:4000]
            + "\n\n"
        )
    planning_retry_feedback = str((state.get("planning_review_feedback") or {}).get("pm") or "").strip()
    planning_retry_block = ""
    if planning_retry_feedback:
        planning_retry_block = (
            "\n[Reviewer feedback from previous PM attempt — fix all issues below before returning a new PM artifact]\n"
            + planning_retry_feedback[:4000]
            + "\n\n"
        )
    user_input = (
        mem
        + xmem
        + ctx
        + _swarm_prompt_prefix(state)
        + _documentation_locale_line(state)
        + _web_research_guidance_block(state, role="pm")
        + planning_mcp_tool_instruction(state)
        + _ca_block
        + _evidence_block
        + planning_retry_block
        + raw_input
    )
    cfg = (state.get("agent_config") or {}).get("pm") or {}

    if os.getenv("SWARM_DEEP_PLANNING", "0") == "1":
        try:
            from backend.App.orchestration.application.use_cases.deep_planning import DeepPlanner
            task_id = os.getenv("SWARM_CURRENT_TASK_ID", "unknown")
            workspace_root = str(state.get("workspace_root") or os.getenv("SWARM_WORKSPACE_ROOT", ""))
            _stream_automation_emit(state, "deep_planning", "deep_planning: scanning workspace (stage 1/5)…")
            plan = DeepPlanner().analyze(
                task_id=task_id,
                task_spec=user_input,
                workspace_root=workspace_root,
            )
            if not plan.error:
                _stream_automation_emit(
                    state, "deep_planning",
                    f"deep_planning complete — {len(plan.risks)} risks, "
                    f"{len(plan.alternatives)} alternatives, "
                    f"{len(plan.milestones)} milestones. "
                    f"Recommended: {plan.recommended_alternative or 'n/a'}",
                )
                summary = (
                    f"## Deep Planning Analysis\n\n"
                    f"Scan: {plan.scan_summary[:400]}\n"
                    f"Risks: {len(plan.risks)} identified\n"
                    f"Alternatives: {len(plan.alternatives)}\n"
                    f"Milestones: {len(plan.milestones)}\n"
                    f"Recommended: {plan.recommended_alternative}\n\n"
                )
                user_input = summary + user_input
                _log.info("pm_node: deep planning prepended (task=%s)", task_id)
            else:
                _stream_automation_emit(
                    state, "deep_planning",
                    f"deep_planning failed: {plan.error} — proceeding without deep analysis. "
                    "PM output may lack workspace-aware context."
                )
                _log.warning("pm_node: deep planning failed (%s)", plan.error)
                set_ephemeral(state, "deep_planning_error", str(plan.error))
        except Exception as exc:
            _stream_automation_emit(
                state, "deep_planning",
                f"deep_planning exception: {exc} — proceeding without deep analysis. "
                "PM output may lack workspace-aware context."
            )
            _log.warning("pm_node: deep planning exception (%s)", exc)
            set_ephemeral(state, "deep_planning_error", str(exc))

    agent = PMAgent(
        system_prompt_path_override=cfg.get("prompt_path") or cfg.get("prompt"),
        model_override=_env_model_override("SWARM_PM_MODEL", cfg.get("model"), cfg.get("_planner_capability")),
        environment_override=cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, cfg),
        **_remote_api_client_kwargs_for_role(state, cfg),
    )
    pm_output, _, _ = _llm_planning_agent_run(agent, user_input, state)
    _stripped_pm = (pm_output or "").strip()
    _tool_call_count = _stripped_pm.count("<tool_call>")
    _non_tool_chars = len(_stripped_pm) - sum(
        len(blk) for blk in _stripped_pm.split("<tool_call>")[1:]
    )
    if _tool_call_count >= 1 and _non_tool_chars < 200:
        _log.warning(
            "PM output appears to be raw tool_call envelopes (tool_call_count=%d, non_tool_chars=%d) — retrying",
            _tool_call_count,
            _non_tool_chars,
        )
        _retry_input = (
            user_input
            + "\n\n[CRITICAL] Your previous response contained only raw <tool_call> XML blocks and no readable text. "
            "Do NOT emit tool call syntax. Produce a structured human-readable task list directly."
        )
        pm_output, _, _ = _llm_planning_agent_run(agent, _retry_input, state)
    _pm_min_chars = 300
    if pm_output and len(pm_output.strip()) < _pm_min_chars:
        _log.warning(
            "PM output too short (%d chars < %d) — retrying with explicit task-list instruction",
            len(pm_output.strip()), _pm_min_chars,
        )
        _retry_input = (
            user_input
            + "\n\n[CRITICAL] Your previous response was too brief and contained no task list. "
            "You MUST produce a structured list of development tasks with acceptance criteria. "
            "Do NOT describe what you plan to do — output the actual tasks NOW."
        )
        pm_output, _, _ = _llm_planning_agent_run(agent, _retry_input, state)
    if (pm_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "pm", pm_output)
    memory_artifact = _pm_memory_artifact(plan_ctx, pm_output)
    return {
        "pm_output": pm_output,
        "pm_model": agent.used_model,
        "pm_provider": agent.used_provider,
        "pm_memory_artifact": memory_artifact,
    }


def review_pm_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_pm_node")
    pm_art = embedded_review_artifact(
        state,
        state.get("pm_output"),
        log_node="review_pm_node",
        part_name="pm_output",
        env_name="SWARM_REVIEW_PM_OUTPUT_MAX_CHARS",
        default_max=60_000,
    )
    prompt = (
        "Step: pm (Project Manager).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] PM did not introduce a NEW technology stack — PM may reference or confirm a stack "
        "already present in the workspace (e.g. existing project files, wiki, code_analysis, "
        "or a previous Architecture ADR); only an unsupported NEW choice by PM is a violation\n"
        "[ ] Tasks are decomposed into concrete subtasks with priorities\n"
        "[ ] Each task has clear acceptance/readiness criteria\n"
        "[ ] Scope is realistic (not a copy of raw user input)\n\n"
        f"User task:\n{user_block}\n\n"
        f"PM artifact:\n{pm_art}"
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_pm",
        prompt=prompt,
        output_key="pm_review_output",
        model_key="pm_review_model",
        provider_key="pm_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_pm_node(state: PipelineState) -> dict[str, Any]:
    bundle = f"PM:\n{state['pm_output']}\n\nReview:\n{state['pm_review_output']}"
    agent = _make_human_agent(state, "pm")
    return {"pm_human_output": agent.run(bundle)}
