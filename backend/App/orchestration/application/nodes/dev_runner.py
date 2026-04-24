from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
from backend.App.orchestration.application.pipeline.ephemeral_state import ephemeral_as_dict
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _dev_sibling_tasks_block,
    _dev_workspace_instructions,
    _documentation_locale_line,
    _effective_spec_for_build,
    _llm_build_agent_run,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _remote_api_client_kwargs_for_role,
    _should_use_mcp_for_workspace,
    _skills_extra_for_role_cfg,
    _stream_automation_emit,
    _stream_progress_emit,
    _swarm_languages_line,
    _swarm_prompt_prefix,
    build_phase_pipeline_user_context,
    pipeline_user_task,
    run_with_self_verify,
)
from backend.App.orchestration.application.nodes._prompt_builders import (
    format_conventions_for_prompt,
    find_reference_file,
    spec_summary_for_subtask,
)
from backend.App.orchestration.application.nodes.dev_subtasks import (
    _dev_devops_max_chars,
    _dev_spec_max_chars,
)

logger = logging.getLogger(__name__)


def _path_covered(expected: str, produced_paths: list[str]) -> bool:
    exp_norm = expected.lstrip("./").replace("\\", "/")
    exp_basename = exp_norm.rsplit("/", 1)[-1]
    for p in produced_paths:
        p_norm = p.lstrip("./").replace("\\", "/")
        if p_norm == exp_norm:
            return True
        if p_norm.endswith("/" + exp_norm) or exp_norm.endswith("/" + p_norm):
            return True
        p_basename = p_norm.rsplit("/", 1)[-1]
        if exp_basename and p_basename == exp_basename:
            return True
    return False


def _normalize_produced_path(raw: str, workspace_root: str) -> str:
    if not raw:
        return raw
    normalized = raw.replace("\\", "/")
    if workspace_root:
        ws = workspace_root.rstrip("/").replace("\\", "/") + "/"
        if normalized.startswith(ws):
            return normalized[len(ws):]
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


def _read_last_mcp_writes() -> tuple[int, list[dict[str, Any]]]:
    try:
        from backend.App.integrations.infrastructure.mcp.openai_loop.loop import _last_mcp_write_count
        return (
            int(getattr(_last_mcp_write_count, "count", 0) or 0),
            list(getattr(_last_mcp_write_count, "actions", []) or []),
        )
    except Exception:
        return 0, []


def is_dev_retry_lean(state: Any) -> bool:
    prior_review = bool(str(state.get("dev_review_output") or "").strip())
    swarm_reprompt = bool(str(state.get("_swarm_file_reprompt") or "").strip())
    if not (prior_review or swarm_reprompt):
        return False
    env_value = os.environ.get("SWARM_DEV_RETRY_LEAN", "1").strip().lower()
    return env_value not in ("0", "false", "no", "off")


def is_progressive_context(state: Any) -> bool:
    if os.environ.get("SWARM_PROGRESSIVE_CONTEXT", "0").strip().lower() not in (
        "1", "true", "yes"
    ):
        return False
    from backend.App.orchestration.application.nodes._prompt_builders import (
        _workspace_context_mode_normalized,
    )
    return _workspace_context_mode_normalized(state) == "retrieve"


def _run_dev_subtask(
    i: int,
    task: dict[str, Any],
    *,
    state: PipelineState,
    task_count: int,
    tasks: list[dict[str, Any]],
    spec: str,
    ca: dict[str, Any],
    ca_block: str,
    conventions_block: str,
    dev_ctx: str,
    langs: str,
    ws_block: str,
    swarm_file_guidance: str,
    devops_block: str,
    apply_writes: bool,
    use_mcp: bool,
) -> tuple[int, str, str, str, int, list[dict[str, Any]], dict[str, Any]]:
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
        model_override=_cfg_model(dev_cfg),
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

    from backend.App.orchestration.application.context.state_searcher import (
        search_context as _search_context,
    )
    _sem_ctx = _search_context(ephemeral_as_dict(state), f"{title} {scope[:200]}", top_k=2)
    if not scope:
        scope = (
            "Implement everything necessary according to the full specification within the meaning of this subtask "
            f"({title}); if the scope is general — organize the work logically."
        )
    from backend.App.orchestration.application.context.context_budget import get_context_budget
    _dev_budget = get_context_budget(
        "dev",
        state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None,
    )
    user_ctx = pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)
    prior_review = (state.get("dev_review_output") or "").strip()
    _swarm_file_reprompt = str(state.get("_swarm_file_reprompt") or "").strip()
    _retry_lean = is_dev_retry_lean(state)
    _progressive = not _retry_lean and is_progressive_context(state)
    if _retry_lean:
        mem = ""
        retry_lean_note = (
            "[Retry context] This is a re-run of the subtask after reviewer "
            "feedback (or format-enforcement). The pattern/knowledge/sibling "
            "context from the first run is unchanged — focus on the feedback "
            "block below.\n\n"
        )
        logger.info(
            "pipeline dev subtask %d/%d retry-lean: pattern/knowledge/sibling blocks dropped",
            i + 1, task_count,
        )
    elif _progressive:
        mem = ""
        retry_lean_note = (
            "[Progressive context — M-7] Pattern/knowledge/sibling blocks were not "
            "pre-loaded (SWARM_PROGRESSIVE_CONTEXT=1, MCP mode). "
            "Use the read_file tool to access .swarm/ if you need memory context.\n\n"
        )
        logger.info(
            "pipeline dev subtask %d/%d progressive-context: memory/knowledge blocks skipped",
            i + 1, task_count,
        )
    else:
        mem = format_pattern_memory_block(
            state,
            f"{pipeline_user_task(state)}\n{title}\n{scope}",
            max_chars=_dev_budget.pattern_memory_chars,
        )
        retry_lean_note = ""
    prior_feedback_block = ""
    if prior_review:
        prior_feedback_block = (
            "\n\n## Prior review feedback (NEEDS_WORK — address all issues below)\n"
            f"{prior_review[:2000]}\n"
            "\n## CRITICAL OUTPUT FORMAT REQUIREMENT\n"
            "You MUST write ALL code inside <swarm_file path=\"...\">...</swarm_file> tags "
            "or <swarm_patch path=\"...\">SEARCH/REPLACE blocks</swarm_patch>.\n"
            "Text descriptions WITHOUT code tags will be REJECTED.\n"
        )
    if _swarm_file_reprompt:
        prior_feedback_block += (
            "\n\n## IMPORTANT: File tagging correction required\n"
            f"{_swarm_file_reprompt}\n"
        )
    _use_summary = os.environ.get("SWARM_DEV_SUBTASK_SPEC_SUMMARY", "1").strip() != "0"
    if _use_summary and scope:
        subtask_spec = spec_summary_for_subtask(spec, scope)
        logger.info(
            "dev subtask %d/%d: using spec summary (%d chars) instead of full spec (%d chars)",
            i + 1, task_count, len(subtask_spec), len(spec),
        )
    else:
        subtask_spec = spec
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
        + _documentation_locale_line(state)
        + f"{langs}"
        + swarm_file_guidance
        + ("" if (_retry_lean or _progressive) else _project_knowledge_block(state, step_id="dev"))
        + ("" if (_retry_lean or _progressive) else _dev_sibling_tasks_block(tasks, i))
        + retry_lean_note
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
        + (f"\n## Relevant pipeline context\n{_sem_ctx}\n" if _sem_ctx else "")
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
    from backend.App.orchestration.application.enforcement.self_verify import _verify_enabled
    if _verify_enabled():
        _stream_automation_emit(
            state, "self_verify",
            f"self_verify: checking subtask {i + 1}/{task_count} output…",
        )
    output = run_with_self_verify(_agent_run_for_verify, task_spec_for_verify, prompt)

    from backend.App.integrations.infrastructure.mcp.openai_loop._dispatch import sanitize_control_tokens
    if output:
        output = sanitize_control_tokens(output)

    _refusal_env = os.getenv("SWARM_REFUSAL_MARKERS", "").strip()
    _REFUSAL_MARKERS: tuple[str, ...] = (
        tuple(m.strip().lower() for m in _refusal_env.split(",") if m.strip())
        if _refusal_env
        else (
            "i'm sorry, but i can't",
            "i cannot continue",
            "i'm unable to",
            "i apologize, but i",
            "as an ai language model",
            "i can't assist with",
        )
    )
    if output and any(m in output.lower() for m in _REFUSAL_MARKERS):
        logger.warning(
            "pipeline dev subtask %d/%d: model refusal detected — retrying with "
            "directive prompt. task_id=%s preview=%r",
            i + 1, task_count,
            (state.get("task_id") or "")[:36],
            output[:120],
        )
        _stream_progress_emit(
            state,
            f"Dev {i + 1}/{task_count}: model refusal detected — retrying with directive prompt…",
        )
        _refusal_retry_prompt = (
            f"[CRITICAL] You MUST write code for this task. Do NOT refuse or apologize.\n"
            f"Task: {title}\n{scope}\n\n"
            f"Write ONLY code using <swarm_file> or <swarm_patch> tags. No explanations.\n"
            + swarm_file_guidance
        )
        output = _agent_run_for_verify(_refusal_retry_prompt)
        if output:
            output = sanitize_control_tokens(output)

    _enforce = os.getenv("SWARM_ENFORCE_WRITE_FORMAT", "1").strip() in ("1", "true", "yes")
    _has_swarm_tags = bool(re.search(r"<swarm_file|<swarm_patch|<swarm_udiff", output or "", re.IGNORECASE))
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

    _dialogue_rounds = int(os.environ.get("SWARM_DEV_DIALOGUE_ROUNDS", "0").strip() or "0")
    if _dialogue_rounds > 1 and output and apply_writes and not (output or "").startswith("[EC-3"):
        try:
            from backend.App.orchestration.application.agents.dialogue_loop import (
                DialogueLoop as _DialogueLoop,
            )
            from backend.App.orchestration.domain.quality_gate_policy import (
                extract_verdict as _extract_verdict,
            )

            _reviewer_for_dia = _make_reviewer_agent(state)
            _first_call_done: list[bool] = [False]
            _first_output = output

            class _DevRoundAdapter:
                def run(self, p: str, **_kw: Any) -> str:
                    if not _first_call_done[0]:
                        _first_call_done[0] = True
                        return _first_output
                    revised, *_ = _llm_build_agent_run(agent, p, state)
                    return revised or _first_output

            _dialogue = _DialogueLoop(max_rounds=_dialogue_rounds)
            _dia = _dialogue.run(
                agent_a=_DevRoundAdapter(),
                agent_b=_reviewer_for_dia,
                initial_input=prompt,
                extract_verdict_fn=_extract_verdict,
                progress_queue=state.get("_stream_progress_queue"),
                step_label=f"dev[{subtask_id}]↔reviewer",
            )
            if _dia.final_output:
                output = _dia.final_output
                logger.info(
                    "dev_node: subtask [%s] dialogue done rounds=%d verdict=%s",
                    subtask_id, _dia.rounds_used, _dia.verdict,
                )
        except Exception as _dia_exc:
            logger.warning("dev_node: dialogue loop failed for subtask [%s]: %s — using original output", subtask_id, _dia_exc)

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
    _ca_raw = state.get("code_analysis")
    ca: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    ca_block = ""
    conventions_block = ""
    if not _code_analysis_is_weak(ca):
        ca_block = "\n## Existing code analysis\n" + _compact_code_analysis_for_prompt(ca, max_chars=8000) + "\n"
        conventions_block = format_conventions_for_prompt(ca)
    dev_ctx = _pipeline_context_block(state, "dev")
    langs = _swarm_languages_line(state)
    ws_block = _dev_workspace_instructions(state)
    apply_writes = bool(state.get("workspace_apply_writes"))

    from backend.App.orchestration.application.context.state_searcher import (
        index_state as _index_state,
    )
    _index_state(ephemeral_as_dict(state))

    swarm_file_guidance = ""
    if ws_block.strip():
        if apply_writes and use_mcp:
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
            from backend.App.orchestration.application.context.context_budget import get_context_budget
            _crole_budget = get_context_budget(
                f"crole_{role_name}",
                state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None,
            )
            role_prompt = (
                format_pattern_memory_block(
                    state,
                    f"{pipeline_user_task(state)}\n{role_name}",
                    max_chars=_crole_budget.pattern_memory_chars,
                )
                + dev_ctx
                + _swarm_prompt_prefix(state)
                + _documentation_locale_line(state)
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
        if (merged or "").strip():
            from backend.App.workspace.application.doc_workspace import write_step_wiki
            write_step_wiki(state, "dev", merged)
        return {
            "dev_output": merged,
            "dev_task_outputs": role_outputs,
            "dev_model": dev_model,
            "dev_provider": dev_provider,
        }

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.App.orchestration.application.pipeline.parallel_limits import swarm_max_parallel_tasks

    task_count = len(tasks)
    dev_task_outputs = []
    dev_model, dev_provider = "", ""
    _total_mcp_writes = 0
    _total_mcp_write_actions: list[dict[str, Any]] = []
    subtask_contracts: list[dict[str, Any]] = []

    subtask_kwargs: dict[str, Any] = dict(
        state=state,
        task_count=task_count,
        tasks=tasks,
        spec=spec,
        ca=ca,
        ca_block=ca_block,
        conventions_block=conventions_block,
        dev_ctx=dev_ctx,
        langs=langs,
        ws_block=ws_block,
        swarm_file_guidance=swarm_file_guidance,
        devops_block=devops_block,
        apply_writes=apply_writes,
        use_mcp=use_mcp,
    )

    _force_sequential = os.environ.get("SWARM_DEV_FORCE_SEQUENTIAL", "").strip() in ("1", "true", "yes")
    if not _force_sequential and task_count > 1:
        logger.info(
            "dev_node: running %d subtasks in parallel "
            "(SWARM_DEV_FORCE_SEQUENTIAL=1 to disable). task_id=%s",
            task_count,
            (state.get("task_id") or "")[:36],
        )
        max_workers = min(swarm_max_parallel_tasks(), task_count)
        results: list[Optional[tuple[int, str, str, str, int, list[dict[str, Any]], dict[str, Any]]]] = [None] * task_count
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_to_idx = {executor.submit(_run_dev_subtask, i, task, **subtask_kwargs): i for i, task in enumerate(tasks)}
            for fut in as_completed(fut_to_idx):
                i = fut_to_idx[fut]
                try:
                    _, out_i, m_i, p_i, cnt_i, acts_i, contract_i = fut.result()
                    results[i] = (i, out_i, m_i, p_i, cnt_i, acts_i, contract_i)
                    _total_mcp_writes += cnt_i
                    _total_mcp_write_actions.extend(acts_i)
                except Exception as _fut_exc:
                    logger.error(
                        "dev_node: subtask %d/%d raised an exception — skipping (task_id=%s): %s",
                        i + 1, task_count, (state.get("task_id") or "")[:36], _fut_exc,
                    )
                    results[i] = None
        _parallel_models: list[str] = []
        _parallel_providers: list[str] = []
        for _r in results:
            if _r is None:
                dev_task_outputs.append("")
                subtask_contracts.append({})
            else:
                _, _o, _m, _p, _, _, _c = _r
                dev_task_outputs.append(_o)
                subtask_contracts.append(_c)
                _parallel_models.append(_m or "")
                _parallel_providers.append(_p or "")
        if _parallel_models:
            dev_model = " | ".join(m for m in _parallel_models if m) or dev_model
        if _parallel_providers:
            dev_provider = " | ".join(p for p in _parallel_providers if p) or dev_provider
    else:
        for i, task in enumerate(tasks):
            _, output, dev_model, dev_provider, _count, _actions, contract = _run_dev_subtask(i, task, **subtask_kwargs)
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
    if (merged or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "dev", merged)
    return {
        "dev_output": merged,
        "dev_task_outputs": dev_task_outputs,
        "dev_model": dev_model,
        "dev_provider": dev_provider,
        "dev_mcp_write_count": _total_mcp_writes,
        "dev_mcp_write_actions": _total_mcp_write_actions,
        "dev_subtask_contracts": subtask_contracts,
    }
