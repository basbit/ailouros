"""Dev pipeline nodes: dev_lead, dev, review_dev, human_dev."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.orchestration.infrastructure.agents.dev_lead_agent import DevLeadAgent
from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
from backend.App.orchestration.application.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline_state import PipelineState

from backend.App.orchestration.application.nodes._shared import (
    _bare_repo_scaffold_instruction,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _dev_sibling_tasks_block,
    _dev_workspace_instructions,
    _effective_spec_for_build,
    _env_model_override,
    _llm_build_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _remote_api_client_kwargs_for_role,
    _should_use_mcp_for_workspace,
    _skills_extra_for_role_cfg,
    _spec_for_build_mcp_safe,
    _stream_progress_emit,
    _swarm_languages_line,
    _swarm_prompt_prefix,
    build_phase_pipeline_user_context,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    pipeline_user_task,
    run_with_self_verify,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    _run_agent_with_boundary as _canonical_run_agent_with_boundary,
    _validate_agent_boundary as _canonical_validate_agent_boundary,
    format_conventions_for_prompt,
    find_reference_file,
)
from backend.App.orchestration.application.nodes.dev_subtasks import (
    _dev_devops_max_chars,
    _dev_spec_max_chars,
    normalize_dev_qa_tasks_to_count,
    parse_dev_lead_plan,
    parse_dev_qa_task_plan,
    read_dev_qa_task_count_target,
)
from backend.App.orchestration.application.nodes.dev_review import (
    _review_dev_output_max_chars,
    _review_spec_max_chars,
    human_dev_node,
    review_dev_node,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Own public API
    "dev_lead_node",
    "review_dev_lead_node",
    "human_dev_lead_node",
    "dev_node",
    "review_dev_node",
    "human_dev_node",
    "parse_dev_qa_task_plan",
    "read_dev_qa_task_count_target",
    "normalize_dev_qa_tasks_to_count",
    # Re-exported from dev_subtasks
    "_dev_devops_max_chars",
    "_dev_spec_max_chars",
    "parse_dev_lead_plan",
    # Re-exported from dev_review
    "_review_dev_output_max_chars",
    "_review_spec_max_chars",
]


def _run_agent_with_boundary(state: PipelineState, agent: Any, prompt: str) -> str:
    """Local alias kept for backward-compatible test seams around boundary validation."""
    return _canonical_run_agent_with_boundary(state, agent, prompt)


def _validate_agent_boundary(state: PipelineState, agent: Any, prompt: str, output: str) -> None:
    """Backward-compatible alias for tests patching the old local boundary hook."""
    _canonical_validate_agent_boundary(state, agent, prompt, output)


def _small_task_profile(task: dict[str, Any]) -> dict[str, Any]:
    expected_paths = [str(item or "").strip() for item in (task.get("expected_paths") or []) if str(item or "").strip()]
    dependencies = [str(item or "").strip() for item in (task.get("dependencies") or []) if str(item or "").strip()]
    is_small = len(expected_paths) <= 2 and len(dependencies) <= 2
    return {
        "enabled": is_small,
        "spec_max_chars": int(os.environ.get("SWARM_DEV_SMALL_TASK_SPEC_MAX_CHARS", "6000")),
        "code_analysis_max_chars": int(os.environ.get("SWARM_DEV_SMALL_TASK_CODE_ANALYSIS_MAX_CHARS", "2500")),
        "duration_budget_sec": float(os.environ.get("SWARM_DEV_SMALL_TASK_DURATION_BUDGET_SEC", "120")),
        "split_recovery_enabled": os.environ.get("SWARM_DEV_SMALL_TASK_SPLIT_RECOVERY", "1").strip() in ("1", "true", "yes"),
        "escalation_model": os.environ.get("SWARM_DEV_SMALL_TASK_ESCALATION_MODEL", "").strip(),
    }


def _small_task_missing_path_batches(missing_paths: list[str]) -> list[list[str]]:
    return [[path] for path in missing_paths if str(path or "").strip()]


def _planning_review_artifact(state: PipelineState) -> dict[str, Any]:
    review_steps = (
        ("review_pm", "pm_review_output"),
        ("review_stack", "stack_review_output"),
        ("review_arch", "arch_review_output"),
    )
    reviews: list[dict[str, str]] = []
    blockers = list(state.get("planning_review_blockers") or [])
    for step_id, output_key in review_steps:
        output = str(state.get(output_key) or "").strip()
        verdict = "OK"
        match = re.search(r"VERDICT\s*:\s*(\w+)", output, re.IGNORECASE)
        if match:
            verdict = match.group(1).upper()
        reviews.append(
            {
                "review_step": step_id,
                "verdict": verdict,
                "review_output": output[:4000],
            }
        )
    return {
        "reviews": reviews,
        "open_blockers": blockers,
    }


def _format_planning_review_artifact(state: PipelineState) -> str:
    return json.dumps(_planning_review_artifact(state), ensure_ascii=False, indent=2)


def _path_covered(expected: str, produced_paths: list[str]) -> bool:
    """Return True if *expected* (workspace-relative) is covered by *produced_paths*.

    Uses three matching strategies (in order):
    1. Exact string match — fast path.
    2. Suffix match — handles cases where produced path has a different prefix
       (e.g. produced = 'src/Foo.php', expected = './src/Foo.php' or vice-versa).
    3. Basename match — last resort for flat file writes without directory context.
    """
    exp_norm = expected.lstrip("./").replace("\\", "/")
    exp_basename = exp_norm.rsplit("/", 1)[-1]
    for p in produced_paths:
        p_norm = p.lstrip("./").replace("\\", "/")
        if p_norm == exp_norm:
            return True
        # suffix: 'src/Foo.php' matches 'workspace/src/Foo.php'
        if p_norm.endswith("/" + exp_norm) or exp_norm.endswith("/" + p_norm):
            return True
        # basename: 'ParserRegistry.php' matches any path ending in that filename
        p_basename = p_norm.rsplit("/", 1)[-1]
        if exp_basename and p_basename == exp_basename:
            return True
    return False


def _normalize_produced_path(raw: str, workspace_root: str) -> str:
    """Return a workspace-relative path for *raw*, regardless of whether it is absolute or relative.

    Examples (workspace_root = '/proj/dvij'):
      '/proj/dvij/src/Foo.php'  → 'src/Foo.php'
      'src/Foo.php'             → 'src/Foo.php'
      '/other/project/Foo.php'  → '/other/project/Foo.php'  (outside workspace — returned as-is)
    """
    if not raw:
        return raw
    normalized = raw.replace("\\", "/")
    if workspace_root:
        ws = workspace_root.rstrip("/").replace("\\", "/") + "/"
        if normalized.startswith(ws):
            return normalized[len(ws):]
        # Also try without trailing slash in case they exactly match
        if normalized == ws.rstrip("/"):
            return "."
    return normalized


def _extract_subtask_workspace_contract(
    state: PipelineState,
    output: str,
    *,
    mcp_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    from backend.App.workspace.infrastructure.patch_parser import apply_workspace_pipeline

    produced_paths: list[str] = []
    workspace_root = str(state.get("workspace_root") or "").strip()
    if workspace_root:
        dry_run = apply_workspace_pipeline(
            output or "",
            Path(workspace_root),
            dry_run=True,
            run_shell=False,
        )
        for key in ("written", "patched", "udiff_applied"):
            for rel in dry_run.get(key, []) or []:
                norm = _normalize_produced_path(rel, workspace_root)
                if norm and norm not in produced_paths:
                    produced_paths.append(norm)
    for action in mcp_actions:
        if not isinstance(action, dict):
            continue
        raw = str(action.get("path") or "").strip()
        norm = _normalize_produced_path(raw, workspace_root)
        if norm and norm not in produced_paths:
            produced_paths.append(norm)
    return {
        "produced_paths": produced_paths,
    }


def dev_lead_node(state: PipelineState) -> dict[str, Any]:
    """Dev Lead after BA+Architect (+ DevOps runbook): create Dev/QA subtasks."""
    agent_config = state.get("agent_config") or {}
    target_n = read_dev_qa_task_count_target(agent_config)
    count_rule = ""
    if target_n is not None:
        count_rule = (
            f"\n\n**Client requirement:** the array must contain **exactly {target_n}** elements "
            f"(exactly {target_n} Dev runs and {target_n} separate QA runs). "
            f'Id: "1"…"{target_n}" or your own short unique ids.\n'
        )
    devops_ctx = (state.get("devops_output") or "").strip()
    devops_block = (
        f"\n\nDevOps context (bootstrap / runbook):\n{devops_ctx}\n"
        if devops_ctx
        else ""
    )
    langs = _swarm_languages_line(state)
    code_hint = ""
    code_analysis_output = (state.get("analyze_code_output") or "").strip()
    if code_analysis_output:
        code_hint = f"\n\nRepository static analysis (summary):\n{code_analysis_output}\n"
    refactor_plan_text = (state.get("refactor_plan_output") or "").strip()
    if refactor_plan_text and len(refactor_plan_text) < 4000:
        code_hint += f"\nCode improvement plan (if any):\n{refactor_plan_text[:3500]}\n"
    # Workspace structure brief (collected by PM evidence prefetch).
    # Dev Lead MUST use this to derive expected_paths from the real directory tree,
    # NOT invent paths that don't match the existing project layout.
    workspace_brief = (state.get("workspace_evidence_brief") or "").strip()
    if workspace_brief:
        code_hint += (
            f"\n\n[Workspace structure and documentation — CRITICAL for expected_paths]\n"
            f"{workspace_brief[:3000]}\n"
            f"RULE: every `expected_paths` entry MUST be placed inside a directory that "
            f"already exists in the workspace tree above. Do NOT invent new top-level "
            f"directories or path conventions — follow the existing project layout exactly.\n"
        )
    planning_reviews_artifact = _planning_review_artifact(state)
    open_blockers = list(planning_reviews_artifact.get("open_blockers") or [])
    unresolved = [
        item for item in list(planning_reviews_artifact.get("reviews") or [])
        if str(item.get("verdict") or "").upper() == "NEEDS_WORK"
    ]
    if open_blockers or unresolved:
        logger.warning(
            "dev_lead_node: planning reviewers have unresolved NEEDS_WORK blockers (%d blockers, %d reviews) — "
            "proceeding with dev_lead; QA will validate",
            len(open_blockers), len(unresolved),
        )

    prompt = (
        _swarm_prompt_prefix(state)
        + "[Pipeline rule] You have the **approved specification** (BA + Architect). "
        "Create a **subtask plan**: each one is **one short Dev run** and a narrow QA run. "
        "Dev/QA are **small fast models** and should not re-process the entire spec.\n\n"
        "**Required:** `development_scope` and `testing_scope` must be **self-contained checklists**: "
        "files/classes/endpoints, steps, readiness criteria; no 'implement per spec'. "
        "Each subtask should take **minutes**, not hours.\n\n"
        "**Independence:** subtasks should be maximally **parallelizable** "
        "(different files/modules; specify dependencies only when absolutely required).\n\n"
        f"{langs}"
        "Decompose by modules/features (typically 2–6; one only if the scope is atomic).\n"
        f"{_bare_repo_scaffold_instruction(state)}"
        "If the repository still lacks runnable automated checks for the **Architect** stack — **first** subtask = "
        "minimal bootstrap (dependencies + first smoke test / lint as appropriate); subsequent ones = features.\n\n"
        "User task:\n"
        f"{pipeline_user_task(state)}\n\n"
        "Approved specification (BA + Architect):\n"
        f"{_spec_for_build_mcp_safe(state)}\n"
        "Planning review status artifact (required context; derive subtasks only from approved artifacts):\n"
        f"{json.dumps(planning_reviews_artifact, ensure_ascii=False, indent=2)}\n"
        f"{devops_block}"
        f"{code_hint}"
        f"{count_rule}"
        "Respond with **only** a JSON object inside a ```json ... ``` block, no text outside.\n"
        "Schema:\n"
        '{'
        '"tasks":[{"id":"short_id","title":"title","development_scope":"what Dev implements (boundaries, files, API)","testing_scope":"what QA verifies (scenarios, criteria)","expected_paths":["workspace-relative file path this subtask must write or edit"],"dependencies":["subtask ids that must complete first"]}],'
        '"deliverables":{"must_exist_files":["path/from/workspace"],"spec_symbols":["ClassName","InterfaceName","methodName"],'
        '"verification_commands":[{"command":"build_gate|spec_gate|consistency_gate|stub_gate|diff_risk_gate","expected":"why this trusted gate must pass"}],'
        '"assumptions":["explicit assumption that still needs verification"],'
        '"production_paths":["workspace-relative file or directory that contains production logic for this task"],'
        '"placeholder_allow_list":[{"path":"workspace-relative file or directory","pattern":"exact placeholder pattern to allow","reason":"why this is explicitly allowed"}]}'
        '}\n'
        "Rules for `deliverables`:\n"
        "- `must_exist_files`: only files that MUST exist after implementation according to approved spec.\n"
        "- `spec_symbols`: only key symbols/contracts that MUST exist after implementation.\n"
        "- `verification_commands`: only trusted verification gate names from the allowed system set.\n"
        "- `assumptions`: explicit unknowns; empty list if none.\n"
        "- `production_paths`: explicit production files/directories where placeholder guardrails apply; use [] only when the approved scope has no production-path implementation.\n"
        "- `placeholder_allow_list`: explicit allow-list for temporary placeholders that are intentionally permitted by the approved task; otherwise [].\n"
        "- Each task must include non-empty `expected_paths` for the concrete files it is expected to touch; use workspace-relative paths only.\n"
        "- Use `dependencies` only when a subtask truly cannot start before another subtask.\n"
    )
    dev_lead_cfg = agent_config.get("dev_lead") or agent_config.get("pm_tasks") or agent_config.get("pm") or {}
    if not isinstance(dev_lead_cfg, dict):
        dev_lead_cfg = {}
    # SWARM_DEV_LEAD_MODEL: model override for dev_lead step (env var fallback when not set in config).
    # Use to route dev_lead to a more powerful reasoning model than the default planning model.
    agent = DevLeadAgent(
        system_prompt_path_override=dev_lead_cfg.get("prompt_path") or dev_lead_cfg.get("prompt"),
        model_override=_env_model_override("SWARM_DEV_LEAD_MODEL", dev_lead_cfg.get("model")),
        environment_override=dev_lead_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, dev_lead_cfg),
        **_remote_api_client_kwargs_for_role(state, dev_lead_cfg),
    )
    dev_lead_output = _run_agent_with_boundary(state, agent, prompt)
    plan = parse_dev_lead_plan(dev_lead_output)
    tasks = plan["tasks"]
    deliverables = plan["deliverables"]
    if tasks and not plan.get("has_deliverables"):
        logger.warning(
            "dev_lead_node: missing canonical `deliverables` object in Dev Lead JSON plan — "
            "continuing with empty defaults; gates will be skipped"
        )
    elif tasks and not plan.get("has_complete_deliverables"):
        logger.warning(
            "dev_lead_node: deliverables object is missing some canonical keys "
            "(must_exist_files, spec_symbols, verification_commands, assumptions, "
            "production_paths, placeholder_allow_list) — continuing with partial deliverables"
        )
    if target_n is not None:
        tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

    # EC-9: workspace boundary guard — reject expected_paths with absolute paths outside workspace_root
    workspace_root_str = str(state.get("workspace_root") or "").strip()
    if workspace_root_str and tasks:
        _abs_violations: list[str] = []
        for _task in tasks:
            for _ep in (_task.get("expected_paths") or []):
                _ep_s = str(_ep or "").strip()
                if os.path.isabs(_ep_s) and not _ep_s.startswith(workspace_root_str):
                    _abs_violations.append(_ep_s)
        if _abs_violations:
            logger.warning(
                "EC-9: dev_lead expected_paths contain absolute paths outside workspace_root=%r: %s — retrying with explicit constraint",
                workspace_root_str,
                _abs_violations[:5],
            )
            _boundary_retry_prompt = (
                prompt
                + f"\n\n[CRITICAL] Your previous response contained expected_paths with absolute filesystem paths "
                f"from a DIFFERENT project (e.g. {_abs_violations[0]!r}). "
                f"You MUST use ONLY workspace-relative paths (relative to workspace_root={workspace_root_str!r}). "
                "Do NOT reference absolute paths or paths from other projects."
            )
            dev_lead_output = _run_agent_with_boundary(state, agent, _boundary_retry_prompt)
            plan = parse_dev_lead_plan(dev_lead_output)
            tasks = plan["tasks"]
            deliverables = plan["deliverables"]
            if target_n is not None:
                tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

    # EC-4: validate dev_lead tasks overlap with PM output
    pm_output = (state.get("pm_output") or "").strip()
    if pm_output and tasks:
        # Extract keywords from PM task lines (### [...] CORE/OPTIONAL — Title)
        _pm_keywords: list[str] = []
        for _m in re.finditer(r"###?\s*\[.*?\]\s*(?:CORE|OPTIONAL)\s*[—–-]\s*(.+)", pm_output):
            _kw = _m.group(1).strip().lower()
            if len(_kw) > 5:
                _pm_keywords.append(_kw[:40])
        if _pm_keywords:
            _dev_titles = " ".join(str(t.get("title", "")).lower() for t in tasks)
            _overlap = sum(1 for kw in _pm_keywords if any(w in _dev_titles for w in kw.split()[:3]))
            if _overlap == 0:
                logger.warning(
                    "EC-4: dev_lead tasks have 0 keyword overlap with PM tasks — possible drift. "
                    "PM keywords: %s, dev_lead titles: %s. Retrying with constraint.",
                    _pm_keywords[:5], [t.get("title", "")[:60] for t in tasks[:5]],
                )
                _retry_prompt = (
                    prompt
                    + "\n\n[CRITICAL] Your subtask list does NOT match the PM-approved tasks. "
                    "You MUST decompose ONLY the tasks listed by PM. Do not invent new features. "
                    f"PM tasks keywords: {', '.join(_pm_keywords[:8])}"
                )
                dev_lead_output = _run_agent_with_boundary(state, agent, _retry_prompt)
                plan = parse_dev_lead_plan(dev_lead_output)
                tasks = plan["tasks"]
                deliverables = plan["deliverables"]
                if tasks and not plan.get("has_deliverables"):
                    logger.warning(
                        "dev_lead_node: retry still missing canonical `deliverables` object — "
                        "continuing with empty defaults"
                    )
                elif tasks and not plan.get("has_complete_deliverables"):
                    logger.warning(
                        "dev_lead_node: retry deliverables object missing some canonical keys — "
                        "continuing with partial deliverables"
                    )
                if target_n is not None:
                    tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

    if not tasks:
        raise RuntimeError(
            "dev_lead_node: failed to parse canonical Dev Lead plan "
            f"(expected JSON object with tasks+deliverables, got {len(dev_lead_output or '')} chars)"
        )
    return {
        "dev_lead_output": dev_lead_output,
        "dev_lead_model": agent.used_model,
        "dev_lead_provider": agent.used_provider,
        "dev_qa_tasks": tasks,
        "deliverables_artifact": deliverables,
        "must_exist_files": list(deliverables.get("must_exist_files") or []),
        "spec_symbols": list(deliverables.get("spec_symbols") or []),
        "production_paths": list(deliverables.get("production_paths") or []),
        "placeholder_allow_list": list(deliverables.get("placeholder_allow_list") or []),
        "planning_review_blockers": list(state.get("planning_review_blockers") or []),
    }


def review_dev_lead_node(state: PipelineState) -> dict[str, Any]:
    user_block = embedded_pipeline_input_for_review(state, log_node="review_dev_lead_node")
    spec_full = _effective_spec_for_build(state)
    spec_art = embedded_review_artifact(
        state,
        spec_full,
        log_node="review_dev_lead_node",
        part_name="specification",
        env_name="SWARM_REVIEW_SPEC_MAX_CHARS",
        default_max=40_000,
        mcp_max=3_000,
    )
    lead_art = embedded_review_artifact(
        state,
        state.get("dev_lead_output"),
        log_node="review_dev_lead_node",
        part_name="dev_lead_output",
        env_name="SWARM_REVIEW_DEV_LEAD_OUTPUT_MAX_CHARS",
        default_max=60_000,
        mcp_max=3_000,
    )
    prompt = (
        "Step: dev_lead (subtask plan Dev/QA after merged specification).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Each subtask has a narrow, single-feature scope (not a copy of the entire spec)\n"
        "[ ] Each subtask includes both a development scope and a testing scope\n"
        "[ ] Each subtask includes explicit expected_paths for the files it is supposed to touch\n"
        "[ ] No subtask mixes multiple unrelated features\n"
        "[ ] Subtask count is proportional to scope (XS task → 1-2 subtasks, not 10+)\n"
        "[ ] A valid JSON object with `tasks` and `deliverables` is present in the output\n"
        "[ ] `deliverables.must_exist_files` lists only files explicitly required by the approved spec\n"
        "[ ] `deliverables.spec_symbols` lists only key contracts/symbols justified by the approved spec\n\n"
        "[ ] `deliverables.production_paths` explicitly marks the production files/directories that placeholder guardrails must protect\n"
        "[ ] `deliverables.placeholder_allow_list` is empty unless the approved task explicitly allows a placeholder with a stated reason\n\n"
        "[ ] The plan is derived only from approved planning artifacts; it does not silently inherit reviewer-rejected assumptions\n\n"
        f"User task:\n{user_block}\n\n"
        f"Specification:\n{spec_art}\n\n"
        f"Planning review status artifact:\n{_format_planning_review_artifact(state)}\n\n"
        f"Dev Lead plan:\n{lead_art}"
    )
    return run_reviewer_or_moa(
        state,
        pipeline_step="review_dev_lead",
        prompt=prompt,
        output_key="dev_lead_review_output",
        model_key="dev_lead_review_model",
        provider_key="dev_lead_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
    )


def human_dev_lead_node(state: PipelineState) -> dict[str, Any]:
    bundle = (
        f"Dev/QA subtask plan:\n{state['dev_lead_output']}\n\n"
        f"Review:\n{state['dev_lead_review_output']}"
    )
    agent = _make_human_agent(state, "dev_lead")
    return {"dev_lead_human_output": agent.run(bundle)}


def dev_node(state: PipelineState) -> dict[str, Any]:
    use_mcp = _should_use_mcp_for_workspace(state)
    spec_full = _effective_spec_for_build(state)
    spec_limit = _dev_spec_max_chars()
    if len(spec_full) > spec_limit:
        logger.warning(
            "dev_node: spec truncated from %d to %d chars (SWARM_DEV_SPEC_MAX_CHARS=%d) "
            "to stay within LLM context limit. task_id=%s",
            len(spec_full),
            spec_limit,
            spec_limit,
            (state.get("task_id") or "")[:36],
        )
        spec = spec_full[:spec_limit] + "\n…[spec truncated — increase SWARM_DEV_SPEC_MAX_CHARS to see more]"
    else:
        spec = spec_full

    tasks: list[dict[str, Any]] = list(state.get("dev_qa_tasks") or [])
    if not tasks:
        raise RuntimeError(
            "dev_node: missing canonical dev_qa_tasks from dev_lead; "
            "cannot continue without structured subtask plan"
        )
    devops_limit = _dev_devops_max_chars()
    devops_ctx_full = (state.get("devops_output") or "").strip()
    if len(devops_ctx_full) > devops_limit:
        logger.warning(
            "dev_node: devops_output truncated from %d to %d chars (SWARM_DEV_DEVOPS_MAX_CHARS=%d). task_id=%s",
            len(devops_ctx_full),
            devops_limit,
            devops_limit,
            (state.get("task_id") or "")[:36],
        )
        devops_ctx = devops_ctx_full[:devops_limit] + "\n…[devops truncated]"
    else:
        devops_ctx = devops_ctx_full
    devops_block = (
        f"\n\n## DevOps context (bootstrap / runbook)\n{devops_ctx}\n"
        if devops_ctx
        else ""
    )
    ca = state.get("code_analysis") if isinstance(state.get("code_analysis"), dict) else {}
    ca_block = ""
    conventions_block = ""
    if not _code_analysis_is_weak(ca):
        ca_block = "\n## Existing code analysis\n" + _compact_code_analysis_for_prompt(ca, max_chars=8000) + "\n"
        conventions_block = format_conventions_for_prompt(ca)
    dev_ctx = _pipeline_context_block(state, "dev")
    langs = _swarm_languages_line(state)
    ws_block = _dev_workspace_instructions(state)
    apply_writes = bool(state.get("workspace_apply_writes"))
    swarm_file_guidance = ""
    if ws_block.strip():
        if apply_writes and use_mcp:
            # EC-7: MCP tools available — prioritize function calling over text tags
            swarm_file_guidance = (
                "\n\n[FILE WRITE INSTRUCTIONS — priority order]\n"
                "1. **PREFERRED for existing files:** Use `workspace__edit_file(path, edits)` with targeted edits. "
                "Use `workspace__write_file(path, content)` mainly for new files. Always use **absolute paths**.\n"
                "2. **FALLBACK:** If tools fail, use `<swarm_patch>` for existing files and `<swarm_file>` for new files:\n"
                '   `<swarm_patch path="relative/path.ext">` with SEARCH/REPLACE blocks for existing files\n'
                '   `<swarm_file path="relative/path.ext">`…full file…`</swarm_file>`\n'
                "3. **NEVER:** Do not rewrite an existing file with a full-file block unless absolutely necessary. "
                "If you do, include a `<dev_manifest>` with `rewrite_justifications` for that path.\n"
                "4. **NEVER:** Do not write code only in markdown fences without a tool call "
                "or `<swarm_file>` tag — it will **NOT** be saved to disk.\n"
            )
        elif apply_writes:
            # No MCP — use swarm_file tags
            swarm_file_guidance = (
                "\n\n[FILE WRITE INSTRUCTIONS]\n"
                "You have no direct disk access. The orchestrator parses your reply. "
                "Prefer `<swarm_patch>` for existing files and `<swarm_file>` for new files.\n"
                "For partial edits use:\n"
                '<swarm_patch path="relative/path.ext">\n'
                "<<<<<<< SEARCH\nold fragment\n=======\nnew fragment\n>>>>>>> REPLACE\n"
                "</swarm_patch>\n"
                "Only when creating a new file or when a full rewrite is unavoidable, use:\n"
                '<swarm_file path="relative/path.ext">\n'
                "…contents…\n"
                "</swarm_file>\n"
                "If you fully rewrite an existing file, include a `<dev_manifest>` with `rewrite_justifications` for that path.\n"
                "(no `..` in path). Plain ``` without `<swarm_file>`/`<swarm_patch>` will **NOT** be saved to disk.\n"
            )
        else:
            swarm_file_guidance = (
                "\n\n[Local context — no auto-write]\n"
                "The project root is provided for context only; **auto-write to disk is disabled**. "
                "Keep the response **brief**: initialization commands, minimal code snippets in markdown, "
                "and if needed one or two `<swarm_file>` blocks for manual copying. "
                "**Do not** expand the entire project file by file — that was not requested and bloats the response.\n"
            )

    def _read_last_mcp_writes() -> tuple[int, list[dict[str, Any]]]:
        try:
            from backend.App.integrations.infrastructure.mcp.openai_loop.loop import _last_mcp_write_count

            return (
                int(getattr(_last_mcp_write_count, "count", 0) or 0),
                list(getattr(_last_mcp_write_count, "actions", []) or []),
            )
        except Exception:
            return 0, []

    def _one(i: int, task: dict[str, Any]) -> tuple[int, str, str, str, int, list[dict[str, Any]], dict[str, Any]]:
        logger.info(
            "pipeline dev subtask start %d/%d task_id=%s title=%r",
            i + 1,
            task_count,
            (state.get("task_id") or "")[:36],
            str(task.get("title") or "")[:120],
        )
        _stream_progress_emit(
            state,
            f"Dev {i + 1}/{task_count}: «{str(task.get('title') or '')[:72]}» — сборка промпта и агента…",
        )
        dev_cfg = (state.get("agent_config") or {}).get("dev") or {}
        if not isinstance(dev_cfg, dict):
            dev_cfg = {}
        mt_override: Optional[int] = None
        max_tokens_config = dev_cfg.get("max_output_tokens")
        if isinstance(max_tokens_config, int) and max_tokens_config > 0:
            mt_override = max_tokens_config
        elif isinstance(max_tokens_config, str) and max_tokens_config.strip().isdigit():
            token_limit = int(max_tokens_config.strip())
            if token_limit > 0:
                mt_override = token_limit
        agent = DevAgent(
            system_prompt_path_override=dev_cfg.get("prompt_path") or dev_cfg.get("prompt"),
            model_override=dev_cfg.get("model"),
            environment_override=dev_cfg.get("environment"),
            max_output_tokens=mt_override,
            system_prompt_extra=_skills_extra_for_role_cfg(state, dev_cfg),
            **_remote_api_client_kwargs_for_role(state, dev_cfg),
        )
        subtask_id = str(task.get("id") or i + 1)
        title = str(task.get("title") or f"Subtask {i + 1}")
        scope = (task.get("development_scope") or "").strip()
        expected_paths = [str(item or "").strip() for item in (task.get("expected_paths") or []) if str(item or "").strip()]
        small_profile = _small_task_profile(task)
        if not scope:
            scope = (
                "Implement everything necessary according to the full specification within the meaning of this subtask "
                f"({title}); if the scope is general — organize the work logically."
            )
        mem = format_pattern_memory_block(
            state,
            f"{pipeline_user_task(state)}\n{title}\n{scope}",
        )
        user_ctx = pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)
        prior_review = (state.get("dev_review_output") or "").strip()
        prior_feedback_block = ""
        if prior_review:
            prior_feedback_block = (
                "\n\n## Prior review feedback (NEEDS_WORK — address all issues below)\n"
                f"{prior_review[:2000]}\n"
            )
        subtask_spec = spec
        # Cap subtask spec to avoid sending full spec to every subtask LLM call.
        _subtask_spec_max = int(os.environ.get("SWARM_DEV_SUBTASK_SPEC_MAX_CHARS", "0").strip() or "0")
        if _subtask_spec_max > 0 and len(subtask_spec) > _subtask_spec_max:
            logger.info(
                "dev subtask %d/%d: spec capped from %d to %d chars (SWARM_DEV_SUBTASK_SPEC_MAX_CHARS)",
                i + 1, task_count, len(subtask_spec), _subtask_spec_max,
            )
            subtask_spec = subtask_spec[:_subtask_spec_max] + "\n…[subtask spec capped — set SWARM_DEV_SUBTASK_SPEC_MAX_CHARS=0 to disable]"
        subtask_ca_block = ca_block
        if small_profile["enabled"]:
            if len(subtask_spec) > small_profile["spec_max_chars"]:
                subtask_spec = (
                    subtask_spec[: small_profile["spec_max_chars"]]
                    + "\n…[small-task compact spec snapshot]"
                )
            if len(subtask_ca_block) > small_profile["code_analysis_max_chars"]:
                subtask_ca_block = (
                    subtask_ca_block[: small_profile["code_analysis_max_chars"]]
                    + "\n…[small-task compact code analysis]"
                )
        prompt = (
            mem
            + dev_ctx
            + _swarm_prompt_prefix(state)
            + f"{langs}"
            + swarm_file_guidance
            + _project_knowledge_block(state)
            + _dev_sibling_tasks_block(tasks, i)
            + "[Pipeline rule] Implement according to the **stack and boundaries from the Architect section** "
            + "in the spec below; do not substitute with PM/BA assumptions.\n\n"
            + "[Increment] This is a **narrow subtask** for a fast model: do the **minimum** work "
            + "for the block below, do not rewrite the whole project and do not take anything from the spec outside the scope.\n\n"
            + "User task:\n"
            + f"{user_ctx}\n\n"
            + "Full specification (BA + Architect):\n"
            + f"{subtask_spec}\n"
            + f"{devops_block}"
            + f"{subtask_ca_block}\n"
            + f"{conventions_block}"
            + f"{find_reference_file(ca, scope, str(state.get('workspace_root') or ''))}"
            + f"## Development subtask [{subtask_id}] {title}\n"
            + f"{scope}\n"
            + (
                "Expected workspace paths for this subtask (must be written or edited in this run):\n"
                + "\n".join(f"- {path}" for path in expected_paths)
                + "\n\n"
                if expected_paths
                else ""
            )
            + f"{prior_feedback_block}"
            + (
                "[Small-task profile]\n"
                "This subtask has a strict latency budget. Do the smallest correct patch, "
                "touch only the declared files, and prefer one focused edit over broad rewrites.\n\n"
                if small_profile["enabled"]
                else ""
            )
        )
        prompt += ws_block
        logger.info(
            "pipeline dev subtask LLM task_id=%s subtask=%d/%d prompt_chars=%d model=%s apply_writes=%s",
            (state.get("task_id") or "")[:36],
            i + 1,
            task_count,
            len(prompt),
            getattr(agent, "model", ""),
            apply_writes,
        )
        _stream_progress_emit(
            state,
            f"Dev {i + 1}/{task_count}: промпт {len(prompt):,} симв. → HTTP-вызов LLM "
            f"(model={getattr(agent, 'model', '') or '?'})…",
        )
        # K-1: run through self-verify loop if SWARM_SELF_VERIFY=1
        _used_model_ref: list[str] = []
        _used_provider_ref: list[str] = []
        accumulated_mcp_actions: list[dict[str, Any]] = []
        accumulated_mcp_writes = 0

        def _agent_run_for_verify(p: str) -> str:
            out, m, pr = _llm_build_agent_run(agent, p, state)
            _used_model_ref.clear()
            _used_model_ref.append(m or "")
            _used_provider_ref.clear()
            _used_provider_ref.append(pr or "")
            return out

        task_spec_for_verify = f"{pipeline_user_task(state)}\n\n{scope}"
        started_at = time.monotonic()
        output = run_with_self_verify(_agent_run_for_verify, task_spec_for_verify, prompt)

        # EC-3: enforce write format — retry once if no swarm tags and no MCP writes
        _enforce = os.getenv("SWARM_ENFORCE_WRITE_FORMAT", "1").strip() in ("1", "true", "yes")
        _has_swarm_tags = bool(re.search(r"<swarm_file|<swarm_patch|<swarm_udiff", output or "", re.IGNORECASE))
        # Check actual MCP write count (not text heuristic) to avoid false retry
        _mcp_writes, _mcp_actions = _read_last_mcp_writes()
        accumulated_mcp_writes += int(_mcp_writes or 0)
        accumulated_mcp_actions.extend(_mcp_actions)
        if (
            _enforce
            and apply_writes
            and output
            and not _has_swarm_tags
            and _mcp_writes == 0
        ):
            logger.warning(
                "pipeline dev subtask %d/%d: no <swarm_file> or MCP write in output (%d chars, mcp_writes=%d) — retrying with explicit instruction",
                i + 1, task_count, len(output), _mcp_writes,
            )
            _retry_prompt = (
                prompt
                + "\n\n[CRITICAL] Your previous response contained NO file write operations.\n"
                "You MUST write actual code files NOW. Start with the MOST IMPORTANT file for this subtask.\n"
                "If the file already exists, use <swarm_patch> SEARCH/REPLACE blocks. "
                "Use <swarm_file> only for new files or unavoidable full rewrites.\n"
                "Do NOT plan. Do NOT explain. Just write code.\n\n"
                "Example format:\n"
                '<swarm_patch path="src/Service/MyService.php">\n'
                "<<<<<<< SEARCH\nold code\n=======\nnew code\n>>>>>>> REPLACE\n"
                "</swarm_patch>\n"
            )
            output = _agent_run_for_verify(_retry_prompt)

            # P0-3: After retry, check again. If still no writes — mark as EC-3 failure.
            _has_swarm_tags_2 = bool(re.search(r"<swarm_file|<swarm_patch|<swarm_udiff", output or "", re.IGNORECASE))
            _mcp_writes_2, _mcp_actions = _read_last_mcp_writes()
            accumulated_mcp_writes += int(_mcp_writes_2 or 0)
            accumulated_mcp_actions.extend(_mcp_actions)
            if not _has_swarm_tags_2 and _mcp_writes_2 == 0:
                logger.error(
                    "pipeline dev subtask %d/%d: EC-3 retry also produced no writes (%d chars). "
                    "Model cannot complete this subtask. task_id=%s",
                    i + 1, task_count, len(output or ""),
                    (state.get("task_id") or "")[:36],
                )
                output = (
                    f"[EC-3 FAILURE] Dev subtask [{subtask_id}] '{title}' produced NO file writes "
                    f"after retry. The model ({getattr(agent, 'model', '?')}) could not generate code.\n\n"
                    f"--- Original model output (truncated) ---\n"
                    f"{(output or '')[:2000]}\n"
                )
            _mcp_writes = _mcp_writes_2

        used_model = _used_model_ref[0] if _used_model_ref else ""
        used_provider = _used_provider_ref[0] if _used_provider_ref else ""
        contract = _extract_subtask_workspace_contract(
            state,
            output,
            mcp_actions=accumulated_mcp_actions,
        )
        elapsed_sec = max(0.001, time.monotonic() - started_at)
        produced_paths = list(contract.get("produced_paths") or [])
        missing_paths = [path for path in expected_paths if not _path_covered(path, produced_paths)]
        if (
            small_profile["enabled"]
            and elapsed_sec > float(small_profile["duration_budget_sec"])
            and not produced_paths
        ):
            logger.warning(
                "dev_node: small subtask [%s] exceeded duration budget %.1fs without writes (elapsed=%.1fs)",
                subtask_id,
                float(small_profile["duration_budget_sec"]),
                elapsed_sec,
            )
        if apply_writes and expected_paths and missing_paths:
            _retry_prompt = (
                prompt
                + "\n\n[CRITICAL] Your previous response did not write the required paths for this subtask. "
                f"Missing expected_paths: {', '.join(missing_paths)}. "
                "You MUST edit or create those exact files now. "
                "Return executable workspace edits only."
                + (
                    " You are over the small-task budget, so do not re-explain or redesign anything; "
                    "emit the minimum patch immediately."
                    if small_profile["enabled"]
                    and elapsed_sec > float(small_profile["duration_budget_sec"])
                    else ""
                )
            )
            output = _agent_run_for_verify(_retry_prompt)
            _mcp_writes, _mcp_actions = _read_last_mcp_writes()
            accumulated_mcp_writes += int(_mcp_writes or 0)
            accumulated_mcp_actions.extend(_mcp_actions)
            contract = _extract_subtask_workspace_contract(
                state,
                output,
                mcp_actions=accumulated_mcp_actions,
            )
            elapsed_sec = max(0.001, time.monotonic() - started_at)
            produced_paths = list(contract.get("produced_paths") or [])
            missing_paths = [path for path in expected_paths if not _path_covered(path, produced_paths)]
        recovery_strategy = "none"
        if apply_writes and expected_paths and missing_paths and small_profile["enabled"] and small_profile["split_recovery_enabled"]:
            recovery_strategy = "split_then_escalate"
            escalation_model = str(small_profile.get("escalation_model") or "").strip()
            recovery_agent = agent
            if escalation_model and escalation_model != str(getattr(agent, "model", "") or ""):
                recovery_agent = DevAgent(
                    system_prompt_path_override=dev_cfg.get("prompt_path") or dev_cfg.get("prompt"),
                    model_override=escalation_model,
                    environment_override=dev_cfg.get("environment"),
                    max_output_tokens=mt_override,
                    system_prompt_extra=_skills_extra_for_role_cfg(state, dev_cfg),
                    **_remote_api_client_kwargs_for_role(state, dev_cfg),
                )

            outputs = [output]
            for batch in _small_task_missing_path_batches(missing_paths):
                split_prompt = (
                    prompt
                    + "\n\n[LOW-YIELD RECOVERY]\n"
                    + "Previous attempts under-produced writes for this small subtask. "
                    + "Do not redesign the solution. Touch only the following path(s) now:\n"
                    + "\n".join(f"- {path}" for path in batch)
                    + "\n\nReturn only executable workspace edits for those path(s)."
                )
                split_output, used_model, used_provider = _llm_build_agent_run(recovery_agent, split_prompt, state)
                outputs.append(split_output)
                _mcp_writes_split, _mcp_actions_split = _read_last_mcp_writes()
                accumulated_mcp_writes += int(_mcp_writes_split or 0)
                accumulated_mcp_actions.extend(_mcp_actions_split)
                output = "\n\n".join(part for part in outputs if part)
                contract = _extract_subtask_workspace_contract(
                    state,
                    output,
                    mcp_actions=accumulated_mcp_actions,
                )
                elapsed_sec = max(0.001, time.monotonic() - started_at)
                produced_paths = list(contract.get("produced_paths") or [])
                missing_paths = [path for path in expected_paths if not _path_covered(path, produced_paths)]
                if not missing_paths:
                    break
        if apply_writes and expected_paths and missing_paths:
            _strict = os.environ.get("SWARM_DEV_STRICT_EXPECTED_PATHS", "").strip() in ("1", "true", "yes")
            if _strict:
                raise RuntimeError(
                    f"dev_node: subtask [{subtask_id}] {title!r} did not produce required expected_paths: {missing_paths}"
                )
            logger.warning(
                "dev_node: subtask [%s] %r did not produce all expected_paths (missing=%s produced=%s) — "
                "continuing pipeline; QA will validate final state",
                subtask_id,
                title,
                missing_paths,
                produced_paths,
            )
        writes_done = len(produced_paths) + int(accumulated_mcp_writes or 0)
        writes_per_minute = round((writes_done * 60.0) / elapsed_sec, 3)
        artifact_yield = round(
            (len(produced_paths) / max(1, len(expected_paths))) if expected_paths else float(len(produced_paths)),
            3,
        )
        contract.update(
            {
                "subtask_id": subtask_id,
                "title": title,
                "expected_paths": expected_paths,
                "missing_paths": missing_paths,
                "mcp_write_count": accumulated_mcp_writes,
                "elapsed_sec": round(elapsed_sec, 3),
                "writes_done": writes_done,
                "writes_per_minute": writes_per_minute,
                "artifact_yield_per_subtask": artifact_yield,
                "recovery_strategy": recovery_strategy,
                "small_task_profile": {
                    "enabled": bool(small_profile["enabled"]),
                    "duration_budget_sec": float(small_profile["duration_budget_sec"]),
                    "prompt_chars": len(prompt),
                    "escalation_model": str(small_profile.get("escalation_model") or ""),
                },
            }
        )
        logger.info(
            "pipeline dev subtask done %d/%d task_id=%s out_chars=%d",
            i + 1,
            task_count,
            (state.get("task_id") or "")[:36],
            len(output or ""),
        )
        _stream_progress_emit(
            state,
            f"Dev {i + 1}/{task_count}: «{str(task.get('title') or '')[:56]}» — готово ({len(output or '')} симв.)",
        )
        return i, output, used_model, used_provider, accumulated_mcp_writes, accumulated_mcp_actions, contract

    # Named dev roles mode: backend, frontend, mobile, etc.
    raw_roles = (state.get("agent_config") or {}).get("dev_roles")
    dev_roles: list[dict[str, Any]] = [
        r for r in (raw_roles or []) if isinstance(r, dict) and r.get("name")
    ]

    if dev_roles:
        role_outputs: list[str] = []
        role_models: list[str] = []
        role_providers: list[str] = []
        for i, role_cfg in enumerate(dev_roles):
            role_name = str(role_cfg.get("name") or f"role_{i + 1}")
            logger.info(
                "pipeline dev role start %d/%d task_id=%s role=%s model=%s",
                i + 1,
                len(dev_roles),
                (state.get("task_id") or "")[:36],
                role_name,
                str(role_cfg.get("model") or "default"),
            )
            _stream_progress_emit(
                state,
                f"Dev [{role_name}] {i + 1}/{len(dev_roles)}: сборка промпта и агента…",
            )
            mt_override: Optional[int] = None
            max_tokens_config = role_cfg.get("max_output_tokens")
            if isinstance(max_tokens_config, int) and max_tokens_config > 0:
                mt_override = max_tokens_config
            elif isinstance(max_tokens_config, str) and max_tokens_config.strip().isdigit():
                token_limit = int(max_tokens_config.strip())
                if token_limit > 0:
                    mt_override = token_limit
            agent = DevAgent(
                system_prompt_path_override=role_cfg.get("prompt_path") or role_cfg.get("prompt"),
                model_override=role_cfg.get("model"),
                environment_override=role_cfg.get("environment"),
                max_output_tokens=mt_override,
                system_prompt_extra=_skills_extra_for_role_cfg(state, role_cfg),
                **_remote_api_client_kwargs_for_role(state, role_cfg),
            )
            focus = (role_cfg.get("scope") or "").strip()
            focus_block = f"\n[Role focus: {role_name}]\n{focus}\n" if focus else f"\n[Role: {role_name}]\n"
            role_prompt = (
                format_pattern_memory_block(state, f"{pipeline_user_task(state)}\n{role_name}")
                + dev_ctx
                + _swarm_prompt_prefix(state)
                + f"{langs}"
                + swarm_file_guidance
                + focus_block
                + "[Pipeline rule] Implement according to the **stack and boundaries from the Architect section** "
                "in the spec below; do not substitute with PM/BA assumptions.\n\n"
                "User task:\n"
                f"{pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)}\n\n"
                "Full specification (BA + Architect):\n"
                f"{spec}"
                f"{devops_block}"
                f"{ca_block}\n"
            )
            role_prompt += ws_block
            logger.info(
                "pipeline dev role LLM task_id=%s role=%s prompt_chars=%d model=%s apply_writes=%s",
                (state.get("task_id") or "")[:36],
                role_name,
                len(role_prompt),
                getattr(agent, "model", ""),
                apply_writes,
            )
            _stream_progress_emit(
                state,
                f"Dev [{role_name}] {i + 1}/{len(dev_roles)}: промпт {len(role_prompt):,} симв. "
                f"→ HTTP-вызов LLM (model={getattr(agent, 'model', '') or '?'})…",
            )
            output, used_model, used_provider = _llm_build_agent_run(agent, role_prompt, state)
            # EC-3: enforce write format for dev_roles (parity with _one subtask path)
            _enforce_role = os.getenv("SWARM_ENFORCE_WRITE_FORMAT", "1").strip() in ("1", "true", "yes")
            _has_tags_role = bool(re.search(r"<swarm_file|<swarm_patch|<swarm_udiff", output or "", re.IGNORECASE))
            _mcp_w_role = 0
            try:
                from backend.App.integrations.infrastructure.mcp.openai_loop.loop import _last_mcp_write_count
                _mcp_w_role = getattr(_last_mcp_write_count, 'count', 0)
            except Exception as exc:
                logger.debug("EC-3 dev_roles: failed to read _last_mcp_write_count: %s", exc)
            if _enforce_role and apply_writes and output and not _has_tags_role and _mcp_w_role == 0:
                logger.warning(
                    "pipeline dev role %s: no writes (%d chars, mcp_writes=%d) — retrying",
                    role_name, len(output), _mcp_w_role,
                )
                output, used_model, used_provider = _llm_build_agent_run(
                    agent,
                    role_prompt
                    + "\n\n[CRITICAL] Your previous response contained NO file write operations. "
                    "You MUST edit existing files via <swarm_patch> or workspace__edit_file, "
                    "and use <swarm_file> / workspace__write_file mainly for new files.",
                    state,
                )
            logger.info(
                "pipeline dev role done %d/%d task_id=%s role=%s out_chars=%d",
                i + 1,
                len(dev_roles),
                (state.get("task_id") or "")[:36],
                role_name,
                len(output or ""),
            )
            _stream_progress_emit(
                state,
                f"Dev [{role_name}] {i + 1}/{len(dev_roles)}: готово ({len(output or '')} симв.)",
            )
            role_outputs.append(output)
            role_models.append(used_model or "")
            role_providers.append(used_provider or "")

        sections = [
            f"### Dev [{str(role_cfg.get('name') or i + 1)}]\n\n{role_outputs[i]}"
            for i, role_cfg in enumerate(dev_roles)
            if i < len(role_outputs)
        ]
        merged = "\n\n---\n\n".join(sections)
        dev_model = " | ".join(f"[{r.get('name', j + 1)}]{m or '—'}" for j, (r, m) in enumerate(zip(dev_roles, role_models)))
        dev_provider = " | ".join(f"[{r.get('name', j + 1)}]{p or '—'}" for j, (r, p) in enumerate(zip(dev_roles, role_providers)))
        return {
            "dev_output": merged,
            "dev_task_outputs": role_outputs,
            "dev_model": dev_model,
            "dev_provider": dev_provider,
        }

    # Single-agent mode: sequential or parallel depending on topology
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.App.orchestration.application.parallel_limits import swarm_max_parallel_tasks

    task_count = len(tasks)
    dev_task_outputs = []
    dev_model, dev_provider = "", ""
    _total_mcp_writes = 0
    _total_mcp_write_actions: list[dict[str, Any]] = []
    subtask_contracts: list[dict[str, Any]] = []

    topology = (state.get("agent_config") or {}).get("swarm", {}).get("topology", "")
    if topology == "mesh" and task_count > 1:
        max_workers = min(swarm_max_parallel_tasks(), task_count)
        results: list[Optional[tuple[int, str, str, str, int, list[dict[str, Any]], dict[str, Any]]]] = [None] * task_count
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_to_idx = {executor.submit(_one, i, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(fut_to_idx):
                i = fut_to_idx[fut]
                _, output, dev_model, dev_provider, _count, _actions, contract = fut.result()
                results[i] = (i, output, dev_model, dev_provider, _count, _actions, contract)
                _total_mcp_writes += _count
                _total_mcp_write_actions.extend(_actions)
        for _, output, dev_model, dev_provider, _, _, contract in results:
            dev_task_outputs.append(output)
            subtask_contracts.append(contract)
    else:
        for i, task in enumerate(tasks):
            _, output, dev_model, dev_provider, _count, _actions, contract = _one(i, task)
            dev_task_outputs.append(output)
            _total_mcp_writes += _count
            _total_mcp_write_actions.extend(_actions)
            subtask_contracts.append(contract)

    sections = []
    for subtask_idx, task in enumerate(tasks):
        subtask_id = str(task.get("id") or subtask_idx + 1)
        title = str(task.get("title") or f"Subtask {subtask_idx + 1}")
        task_output = dev_task_outputs[subtask_idx] if subtask_idx < len(dev_task_outputs) else ""
        sections.append(f"### [{subtask_id}] {title}\n\n{task_output}")
    merged = "\n\n---\n\n".join(sections)
    if _total_mcp_writes > 0:
        logger.info("pipeline dev: total MCP write tool calls across subtasks: %d", _total_mcp_writes)
    return {
        "dev_output": merged,
        "dev_task_outputs": dev_task_outputs,
        "dev_model": dev_model,
        "dev_provider": dev_provider,
        "dev_mcp_write_count": _total_mcp_writes,
        "dev_mcp_write_actions": _total_mcp_write_actions,
        "dev_subtask_contracts": subtask_contracts,
    }
