from __future__ import annotations

import logging
import os
import re
import time
from string import Template
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.dev_agent import DevAgent
from backend.App.integrations.infrastructure.pattern_memory import format_pattern_memory_block
from backend.App.orchestration.application.enforcement.enforcement_policy import (
    dev_runner_policy,
    swarm_env_strings_that_mean_enabled,
)
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
from backend.App.orchestration.application.nodes._dev_runner_paths import (
    extract_subtask_workspace_contract as _extract_subtask_workspace_contract,
    is_path_covered as _path_covered,
)
from backend.App.orchestration.application.nodes._dev_runner_small_task import (
    read_last_mcp_writes as _read_last_mcp_writes,
    small_task_missing_path_batches as _small_task_missing_path_batches,
    small_task_profile as _small_task_profile,
)
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


def _prompt_section(section_name: str) -> dict[str, Any]:
    value = load_app_config_json("prompt_fragments.json").get(section_name)
    if not isinstance(value, dict):
        raise RuntimeError(f"prompt_fragments.{section_name} is not configured")
    return value


def _prompt_text(section_name: str, key: str) -> str:
    value = str(_prompt_section(section_name).get(key) or "")
    if not value:
        raise RuntimeError(f"prompt_fragments.{section_name}.{key} is empty")
    return value


def _render_prompt_text(section_name: str, key: str, **values: Any) -> str:
    return Template(_prompt_text(section_name, key)).safe_substitute(**values)


def _dev_runner_prompt(key: str, **values: Any) -> str:
    return _render_prompt_text("dev_runner_prompts", key, **values)


def _dev_file_write_guidance(key: str) -> str:
    return _prompt_text("dev_file_write_guidance", key)


def _configured_bool(environment_key_name: str, default_key: str) -> bool:
    policy = dev_runner_policy()
    environment_key = str(policy.get(environment_key_name) or "").strip()
    environment_value = os.environ.get(environment_key, "").strip().lower() if environment_key else ""
    if environment_value:
        return environment_value in swarm_env_strings_that_mean_enabled()
    return bool(policy.get(default_key))


def _configured_int(environment_key_name: str, default_key: str) -> int:
    policy = dev_runner_policy()
    environment_key = str(policy.get(environment_key_name) or "").strip()
    environment_value = os.environ.get(environment_key, "").strip() if environment_key else ""
    if environment_value:
        try:
            return int(environment_value)
        except ValueError:
            return int(policy.get(default_key) or 0)
    return int(policy.get(default_key) or 0)


def _workspace_action_pattern() -> re.Pattern[str]:
    pattern = str(dev_runner_policy().get("workspace_action_pattern") or "")
    if not pattern:
        raise RuntimeError("pipeline_enforcement_policy.dev_runner.workspace_action_pattern is empty")
    return re.compile(pattern, re.IGNORECASE)


def _configured_refusal_markers() -> tuple[str, ...]:
    policy = dev_runner_policy()
    environment_key = str(policy.get("refusal_markers_environment_key") or "").strip()
    environment_value = os.environ.get(environment_key, "").strip() if environment_key else ""
    if environment_value:
        return tuple(
            marker.strip().lower()
            for marker in environment_value.split(",")
            if marker.strip()
        )
    configured_markers = policy.get("refusal_markers")
    if not isinstance(configured_markers, list):
        return tuple()
    return tuple(
        str(marker).strip().lower()
        for marker in configured_markers
        if str(marker).strip()
    )


def tasks_share_expected_paths(tasks: list[dict[str, Any]]) -> bool:
    owners: dict[str, str] = {}
    for idx, task in enumerate(tasks):
        task_id = str(task.get("id") or idx + 1)
        for raw_path in task.get("expected_paths") or []:
            path = str(raw_path or "").strip().replace("\\", "/")
            if not path:
                continue
            previous_owner = owners.get(path)
            if previous_owner is not None and previous_owner != task_id:
                return True
            owners[path] = task_id
    return False


def is_dev_retry_lean(state: Any) -> bool:
    prior_review = bool(str(state.get("dev_review_output") or "").strip())
    swarm_reprompt = bool(str(state.get("_swarm_file_reprompt") or "").strip())
    if not (prior_review or swarm_reprompt):
        return False
    return _configured_bool("retry_lean_environment_key", "retry_lean_default")


def is_progressive_context(state: Any) -> bool:
    if not _configured_bool("progressive_context_environment_key", "progressive_context_default"):
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
        scope = _dev_runner_prompt("empty_scope_template", title=title)
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
        retry_lean_note = _dev_runner_prompt("retry_lean_note")
        logger.info(
            "pipeline dev subtask %d/%d retry-lean: pattern/knowledge/sibling blocks dropped",
            i + 1, task_count,
        )
    elif _progressive:
        mem = ""
        retry_lean_note = _dev_runner_prompt("progressive_context_note")
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
        prior_feedback_block = _dev_runner_prompt(
            "prior_feedback_template",
            prior_review=prior_review[:2000],
        )
    if _swarm_file_reprompt:
        prior_feedback_block += _dev_runner_prompt(
            "swarm_file_reprompt_template",
            reprompt=_swarm_file_reprompt,
        )
    _use_summary = _configured_bool(
        "subtask_spec_summary_environment_key",
        "subtask_spec_summary_default",
    )
    if _use_summary and scope:
        subtask_spec = spec_summary_for_subtask(spec, scope)
        logger.info(
            "dev subtask %d/%d: using spec summary (%d chars) instead of full spec (%d chars)",
            i + 1, task_count, len(subtask_spec), len(spec),
        )
    else:
        subtask_spec = spec
    _subtask_spec_max = _configured_int(
        "subtask_spec_max_chars_environment_key",
        "subtask_spec_max_chars_default",
    )
    if _subtask_spec_max > 0 and len(subtask_spec) > _subtask_spec_max:
        subtask_spec_limit_key = str(
            dev_runner_policy().get("subtask_spec_max_chars_environment_key") or ""
        ).strip()
        logger.info(
            "dev subtask %d/%d: spec capped from %d to %d chars (%s)",
            i + 1, task_count, len(subtask_spec), _subtask_spec_max, subtask_spec_limit_key,
        )
        subtask_spec = subtask_spec[:_subtask_spec_max] + _dev_runner_prompt(
            "subtask_spec_capped_suffix_template",
            environment_key=subtask_spec_limit_key,
        )
    subtask_ca_block = ca_block
    if small_profile["enabled"]:
        if len(subtask_spec) > small_profile["spec_max_chars"]:
            subtask_spec = (
                subtask_spec[: small_profile["spec_max_chars"]]
                + _dev_runner_prompt("small_task_compact_spec_suffix")
            )
        if len(subtask_ca_block) > small_profile["code_analysis_max_chars"]:
            subtask_ca_block = (
                subtask_ca_block[: small_profile["code_analysis_max_chars"]]
                + _dev_runner_prompt("small_task_compact_code_analysis_suffix")
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
        + _dev_runner_prompt("pipeline_rule")
        + _dev_runner_prompt("increment_rule")
        + _dev_runner_prompt("user_task_template", user_context=user_ctx)
        + _dev_runner_prompt("full_spec_template", spec=subtask_spec)
        + f"{devops_block}"
        + f"{subtask_ca_block}\n"
        + f"{conventions_block}"
        + f"{find_reference_file(ca, scope, str(state.get('workspace_root') or ''))}"
        + (
            _dev_runner_prompt("relevant_pipeline_context_template", context=_sem_ctx)
            if _sem_ctx else ""
        )
        + _dev_runner_prompt(
            "development_subtask_template",
            subtask_id=subtask_id,
            title=title,
            scope=scope,
        )
        + (
            _dev_runner_prompt(
                "expected_paths_template",
                expected_paths="\n".join(f"- {path}" for path in expected_paths),
            )
            if expected_paths
            else ""
        )
        + f"{prior_feedback_block}"
        + (
            _dev_runner_prompt("small_task_profile")
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

    refusal_markers = _configured_refusal_markers()
    if output and any(marker in output.lower() for marker in refusal_markers):
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
        _refusal_retry_prompt = _dev_runner_prompt(
            "refusal_retry_prompt_template",
            title=title,
            scope=scope,
            file_write_guidance=swarm_file_guidance,
        )
        output = _agent_run_for_verify(_refusal_retry_prompt)
        if output:
            output = sanitize_control_tokens(output)

    _enforce = _configured_bool("enforce_write_format_environment_key", "enforce_write_format_default")
    _has_swarm_tags = bool(_workspace_action_pattern().search(output or ""))
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
        _retry_prompt = prompt + _dev_runner_prompt("missing_writes_retry_template")
        output = _agent_run_for_verify(_retry_prompt)

        _has_swarm_tags_2 = bool(_workspace_action_pattern().search(output or ""))
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

    _dialogue_rounds = _configured_int(
        "dialogue_rounds_environment_key",
        "dialogue_rounds_default",
    )
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
        small_task_suffix = (
            _dev_runner_prompt("small_task_budget_suffix")
            if small_profile["enabled"]
            and elapsed_sec > float(small_profile["duration_budget_sec"])
            else ""
        )
        _retry_prompt = prompt + _dev_runner_prompt(
            "missing_paths_retry_template",
            missing_paths=", ".join(missing_paths),
            small_task_suffix=small_task_suffix,
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
            split_prompt = prompt + _dev_runner_prompt(
                "split_recovery_template",
                paths="\n".join(f"- {path}" for path in batch),
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
        _strict = _configured_bool(
            "strict_expected_paths_environment_key",
            "strict_expected_paths_default",
        )
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
    devops_block = _dev_runner_prompt(
        "devops_context_template",
        devops_context=devops_ctx,
    ) if devops_ctx else ""
    _ca_raw = state.get("code_analysis")
    ca: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    ca_block = ""
    conventions_block = ""
    if not _code_analysis_is_weak(ca):
        ca_block = _dev_runner_prompt(
            "code_analysis_template",
            code_analysis=_compact_code_analysis_for_prompt(ca, max_chars=8000),
        )
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
            swarm_file_guidance = _dev_file_write_guidance("with_mcp")
        elif apply_writes:
            swarm_file_guidance = _dev_file_write_guidance("without_mcp")
        else:
            swarm_file_guidance = _dev_file_write_guidance("no_auto_write")

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
            role_prompt = _dev_runner_prompt(
                "role_prompt_template",
                memory=format_pattern_memory_block(
                    state,
                    f"{pipeline_user_task(state)}\n{role_name}",
                    max_chars=_crole_budget.pattern_memory_chars,
                ),
                dev_context=dev_ctx,
                swarm_prefix=_swarm_prompt_prefix(state),
                locale=_documentation_locale_line(state),
                languages=f"{langs}",
                file_write_guidance=swarm_file_guidance,
                focus_block=focus_block,
                pipeline_rule=_dev_runner_prompt("pipeline_rule"),
                user_task_block=_dev_runner_prompt(
                    "user_task_template",
                    user_context=(
                        pipeline_user_task(state)
                        if use_mcp
                        else build_phase_pipeline_user_context(state)
                    ),
                ),
                spec_block=_dev_runner_prompt("full_spec_template", spec=spec),
                devops_block=devops_block,
                code_analysis_block=ca_block,
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
            _enforce_role = _configured_bool(
                "enforce_write_format_environment_key",
                "enforce_write_format_default",
            )
            _has_tags_role = bool(_workspace_action_pattern().search(output or ""))
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
                    role_prompt + _dev_runner_prompt("missing_writes_retry_template"),
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

    _force_sequential = _configured_bool(
        "force_sequential_environment_key",
        "force_sequential_default",
    )
    _overlapping_expected_paths = tasks_share_expected_paths(tasks)
    if _overlapping_expected_paths and not _force_sequential:
        logger.warning(
            "dev_node: overlapping expected_paths detected; running subtasks sequentially "
            "to avoid conflicting writes. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        _stream_progress_emit(
            state,
            "Dev subtasks share expected_paths — running sequentially to avoid write conflicts.",
        )
    if not _force_sequential and task_count > 1:
        if _overlapping_expected_paths:
            _force_sequential = True
    if not _force_sequential and task_count > 1:
        force_sequential_key = str(
            dev_runner_policy().get("force_sequential_environment_key") or ""
        ).strip()
        logger.info(
            "dev_node: running %d subtasks in parallel "
            "(%s=1 to disable). task_id=%s",
            task_count,
            force_sequential_key,
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
