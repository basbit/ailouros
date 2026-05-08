from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from backend.App.orchestration.infrastructure.agents.dev_lead_agent import DevLeadAgent
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import (
    _documentation_locale_line,
    _effective_spec_for_build,
    _env_model_override,
    _make_human_agent,
    _make_reviewer_agent,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_prompt_prefix,
    _bare_repo_scaffold_instruction,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _swarm_languages_line,
    _spec_for_build_mcp_safe,
    embedded_pipeline_input_for_review,
    embedded_review_artifact,
    pipeline_user_task,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
    _run_agent_with_boundary as _canonical_run_agent_with_boundary,
    _validate_agent_boundary as _canonical_validate_agent_boundary,
)
from string import Template
from backend.App.orchestration.application.nodes.dev_subtasks import (
    normalize_dev_qa_tasks_to_count,
    parse_dev_lead_plan,
    read_dev_qa_task_count_target,
)
from backend.App.orchestration.application.context.delta_prompt import (
    build_dev_lead_delta_retry_prompt,
    delta_prompting_enabled,
    store_artifact,
)
from backend.App.orchestration.application.contracts.output_contracts import (
    compress_dev_lead_output,
    format_compressed_dev_lead,
    output_compression_enabled,
)
from backend.App.orchestration.application.context.source_research import (
    ensure_source_research,
)

logger = logging.getLogger(__name__)


def _run_agent_with_boundary(state: PipelineState, agent: Any, prompt: str) -> str:
    return _canonical_run_agent_with_boundary(state, agent, prompt)


def _validate_agent_boundary(state: PipelineState, agent: Any, prompt: str, output: str) -> None:
    _canonical_validate_agent_boundary(state, agent, prompt, output)


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


def dev_lead_node(state: PipelineState) -> dict[str, Any]:
    ensure_source_research(state, caller_step="dev_lead")
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
    ca = state.get("code_analysis") if isinstance(state.get("code_analysis"), dict) else {}
    if ca and not _code_analysis_is_weak(ca):
        code_hint = (
            "\n\n## Existing code analysis (use this to place files correctly)\n"
            + _compact_code_analysis_for_prompt(ca, max_chars=8000)
            + "\n"
        )
    refactor_plan_text = (state.get("refactor_plan_output") or "").strip()
    if refactor_plan_text and len(refactor_plan_text) < 4000:
        code_hint += f"\nCode improvement plan (if any):\n{refactor_plan_text[:3500]}\n"
    workspace_brief = str(state.get("workspace_evidence_brief") or "").strip()
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

    research_advisory = str(state.get("research_advisory") or "").strip()
    research_advisory_block = (
        f"\n{research_advisory}\n"
        if research_advisory
        else ""
    )
    source_research = str(state.get("source_research_output") or "").strip()
    if source_research and source_research != "SOURCE_RESEARCH_NOT_REQUIRED":
        if len(source_research) > 6000:
            source_research = source_research[:6000] + "\n…[source research truncated]"
        source_research_block = "\n[External source research brief]\n" + source_research + "\n"
    else:
        source_research_block = ""

    _missing_sections_block = ""
    _missing_sections = state.get("_dev_lead_missing_sections")
    if _missing_sections:
        _missing_sections_block = Template(
            _prompt_fragment("dev_lead_missing_sections_block_template")
        ).safe_substitute(missing=_missing_sections)

    prompt = Template(
        _prompt_fragment("dev_lead_node_prompt_template")
    ).safe_substitute(
        swarm_prefix=_swarm_prompt_prefix(state),
        locale=_documentation_locale_line(state),
        languages=langs,
        bare_repo_scaffold=_bare_repo_scaffold_instruction(state),
        user_task=pipeline_user_task(state),
        spec=_spec_for_build_mcp_safe(state),
        source_research=source_research_block,
        research_advisory=research_advisory_block,
        planning_reviews=json.dumps(
            planning_reviews_artifact, ensure_ascii=False, indent=2,
        ),
        devops_block=devops_block,
        code_hint=code_hint,
        count_rule=count_rule,
        missing_sections=_missing_sections_block,
    )
    _DEV_LEAD_CONFIG_KEYS = ("dev_lead", "pm_tasks", "pm")
    dev_lead_cfg: dict[str, Any] = {}
    for _cfg_key in _DEV_LEAD_CONFIG_KEYS:
        candidate = agent_config.get(_cfg_key)
        if isinstance(candidate, dict) and candidate:
            dev_lead_cfg = candidate
            if _cfg_key != "dev_lead":
                logger.warning(
                    "dev_lead_node: using legacy agent_config[%r] for dev_lead role; "
                    "migrate to agent_config['dev_lead'] (deprecated since v3).",
                    _cfg_key,
                )
            break
    agent = DevLeadAgent(
        system_prompt_path_override=dev_lead_cfg.get("prompt_path") or dev_lead_cfg.get("prompt"),
        model_override=_env_model_override("SWARM_DEV_LEAD_MODEL", dev_lead_cfg.get("model"), dev_lead_cfg.get("_planner_capability")),
        environment_override=dev_lead_cfg.get("environment"),
        system_prompt_extra=_skills_extra_for_role_cfg(state, dev_lead_cfg),
        **_remote_api_client_kwargs_for_role(state, dev_lead_cfg),
    )

    _run_prompt = prompt
    if _missing_sections and delta_prompting_enabled():
        _prev_dl_output = str(state.get("dev_lead_output") or "").strip()
        if _prev_dl_output:
            _missing_list = (
                list(_missing_sections)
                if isinstance(_missing_sections, list)
                else [str(_missing_sections)]
            )
            _run_prompt = build_dev_lead_delta_retry_prompt(
                prev_output=_prev_dl_output,
                missing_sections=_missing_list,
                user_task=pipeline_user_task(state),
            )
            logger.info(
                "dev_lead_node: H-2 delta retry prompt (%d chars) used instead of "
                "full prompt (%d chars) — missing_sections=%s",
                len(_run_prompt), len(prompt), _missing_sections,
            )
        else:
            logger.debug(
                "dev_lead_node: H-2 delta retry skipped — no previous dev_lead_output "
                "in state; using full prompt. missing_sections=%s",
                _missing_sections,
            )

    dev_lead_output = _run_agent_with_boundary(state, agent, _run_prompt)
    plan = parse_dev_lead_plan(dev_lead_output)
    tasks = plan["tasks"]
    deliverables = plan["deliverables"]
    _strict_deliverables = os.environ.get(
        "SWARM_DEV_LEAD_REQUIRE_DELIVERABLES", "0",
    ).strip() == "1"
    if tasks and not plan.get("has_deliverables"):
        msg = (
            "dev_lead_node: Dev Lead JSON plan is missing the canonical `deliverables` "
            "object; downstream verification gates require it."
        )
        if _strict_deliverables:
            raise ValueError(msg)
        logger.warning("%s Continuing with empty defaults; gates will be skipped.", msg)
    elif tasks and not plan.get("has_complete_deliverables"):
        msg = (
            "dev_lead_node: Dev Lead `deliverables` is missing canonical keys "
            "(must_exist_files, spec_symbols, verification_commands, assumptions, "
            "production_paths, placeholder_allow_list)."
        )
        if _strict_deliverables:
            raise ValueError(msg)
        logger.warning("%s Continuing with partial deliverables.", msg)
    if target_n is not None:
        tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

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
            _boundary_retry_prompt = Template(
                _prompt_fragment("dev_lead_boundary_retry_template")
            ).safe_substitute(
                base_prompt=prompt,
                example=repr(_abs_violations[0]),
                workspace_root=repr(workspace_root_str),
            )
            dev_lead_output = _run_agent_with_boundary(state, agent, _boundary_retry_prompt)
            plan = parse_dev_lead_plan(dev_lead_output)
            tasks = plan["tasks"]
            deliverables = plan["deliverables"]
            if target_n is not None:
                tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

    if workspace_root_str and tasks:
        from pathlib import Path as _Path
        _workspace_path = _Path(workspace_root_str)
        _nonexistent_roots: list[str] = []
        for _task in tasks:
            for _ep in (_task.get("expected_paths") or []):
                _ep_s = str(_ep or "").strip()
                if not _ep_s or os.path.isabs(_ep_s):
                    continue
                _ep_parts = _Path(_ep_s).parts
                if len(_ep_parts) > 1:
                    _root_dir = _workspace_path / _ep_parts[0]
                    if not _root_dir.exists():
                        _nonexistent_roots.append(_ep_s)
        if _nonexistent_roots:
            logger.warning(
                "EC-10: dev_lead expected_paths reference non-existent root directories "
                "in workspace_root=%r: %s — retrying with path contract constraint",
                workspace_root_str,
                _nonexistent_roots[:5],
            )
            _existing_dirs = sorted(
                entry.name for entry in _workspace_path.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            )[:12]
            _path_contract_retry_prompt = Template(
                _prompt_fragment("dev_lead_path_contract_retry_template")
            ).safe_substitute(
                base_prompt=prompt,
                example=repr(_nonexistent_roots[0]),
                existing_dirs=str(_existing_dirs),
            )
            dev_lead_output = _run_agent_with_boundary(state, agent, _path_contract_retry_prompt)
            plan = parse_dev_lead_plan(dev_lead_output)
            tasks = plan["tasks"]
            deliverables = plan["deliverables"]
            if target_n is not None:
                tasks = normalize_dev_qa_tasks_to_count(tasks, target_n)

    pm_output = (state.get("pm_output") or "").strip()
    if pm_output and tasks:
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
                _retry_prompt = Template(
                    _prompt_fragment("dev_lead_pm_overlap_retry_template")
                ).safe_substitute(
                    base_prompt=prompt,
                    keywords=", ".join(_pm_keywords[:8]),
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
        logger.warning(
            "dev_lead_node: no canonical JSON tasks parsed from Dev Lead plan "
            "(%d chars) — wrapping full output as a single task",
            len(dev_lead_output or ""),
        )
        tasks = [
            {
                "id": "1",
                "title": "Implementation (auto-wrapped from Dev Lead plan)",
                "description": (dev_lead_output or "").strip()[:8000],
                "expected_paths": [],
            }
        ]
    if (dev_lead_output or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "dev_lead", dev_lead_output)

    _dl_artifact_ref = ""
    _dl_compressed_str = ""
    if output_compression_enabled() and (dev_lead_output or "").strip():
        _dl_compressed = compress_dev_lead_output(dev_lead_output)
        _dl_artifact_ref = _dl_compressed.artifact_ref
        _dl_compressed_str = format_compressed_dev_lead(_dl_compressed)
        logger.debug(
            "dev_lead_node: M-9 compressed output %d chars → %d chars compact "
            "(artifact_ref=%s…)",
            _dl_compressed.char_count,
            len(_dl_compressed_str),
            _dl_artifact_ref[-12:],
        )
    else:
        if (dev_lead_output or "").strip():
            _dl_artifact_ref = store_artifact(dev_lead_output)

    result: dict[str, Any] = {
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
    if _dl_artifact_ref:
        result["dev_lead_output_ref"] = _dl_artifact_ref
    if _dl_compressed_str:
        result["dev_lead_compressed"] = _dl_compressed_str
    return result


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
    prompt = Template(
        _prompt_fragment("review_dev_lead_prompt_template")
    ).safe_substitute(
        user_block=user_block,
        spec_artifact=spec_art,
        planning_reviews=_format_planning_review_artifact(state),
        lead_artifact=lead_art,
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
