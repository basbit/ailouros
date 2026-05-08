from __future__ import annotations

import json
import logging
import os
from string import Template
from typing import Any, Optional

from backend.App.orchestration.application.nodes._prompt_builders import (
    _prompt_fragment,
)

from backend.App.orchestration.infrastructure.agents.qa_agent import QAAgent
from backend.App.orchestration.application.agents.review_moa import run_reviewer_or_moa
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import DefectReport, parse_defect_report
from backend.App.orchestration.application.contracts.output_contracts import (
    compress_qa_output,
    format_compressed_qa,
    output_compression_enabled,
)

from backend.App.orchestration.application.nodes._shared import (
    _cfg_model,
    _code_analysis_is_weak,
    _compact_code_analysis_for_prompt,
    _documentation_locale_line,
    _llm_build_agent_run,
    _make_human_agent,
    _make_reviewer_agent,
    _pipeline_context_block,
    _project_knowledge_block,
    _qa_workspace_verification_instructions,
    _remote_api_client_kwargs_for_role,
    _should_use_mcp_for_workspace,
    _skills_extra_for_role_cfg,
    _stream_progress_emit,
    _swarm_languages_line,
    _swarm_prompt_prefix,
    build_phase_pipeline_user_context,
    embedded_pipeline_input_for_review,
    pipeline_user_task,
)

logger = logging.getLogger(__name__)


def _qa_dev_output_max_chars() -> int:
    env_value = os.getenv("SWARM_QA_DEV_OUTPUT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 80_000


def qa_node(state: PipelineState) -> dict[str, Any]:
    use_mcp = _should_use_mcp_for_workspace(state)
    tasks: list[dict[str, Any]] = list(state.get("dev_qa_tasks") or [])
    dev_task_outputs: list[str] = list(state.get("dev_task_outputs") or [])
    qa_cfg = (state.get("agent_config") or {}).get("qa") or {}
    _qa_compact_limit = int(os.getenv("SWARM_QA_COMPACT_PROMPT_CHARS", "4000").strip())
    if not isinstance(qa_cfg, dict):
        qa_cfg = {}
    langs = _swarm_languages_line(state)
    qa_ctx = _pipeline_context_block(state, "qa")
    _ca_raw = state.get("code_analysis")
    ca: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    ca_block = ""
    if not _code_analysis_is_weak(ca):
        ca_block = "\n[Existing code analysis]\n" + _compact_code_analysis_for_prompt(ca, max_chars=6000) + "\n"
    visual_block = _visual_evidence_prompt_block(state)

    dev_out_limit = _qa_dev_output_max_chars()

    if not tasks:
        _stream_progress_emit(state, "QA: один прогон — сборка промпта и агента…")
        agent = QAAgent(
            system_prompt_path_override=qa_cfg.get("prompt_path") or qa_cfg.get("prompt"),
            model_override=_cfg_model(qa_cfg),
            environment_override=qa_cfg.get("environment"),
            system_prompt_extra=_skills_extra_for_role_cfg(state, qa_cfg),
            **_remote_api_client_kwargs_for_role(state, qa_cfg),
        )
        dev_out_full = state.get("dev_output") or ""
        if len(dev_out_full) > dev_out_limit:
            logger.warning(
                "qa_node: dev_output truncated from %d to %d chars "
                "(SWARM_QA_DEV_OUTPUT_MAX_CHARS=%d). task_id=%s",
                len(dev_out_full),
                dev_out_limit,
                dev_out_limit,
                (state.get("task_id") or "")[:36],
            )
            dev_out_for_prompt = dev_out_full[:dev_out_limit] + "\n…[dev_output truncated]"
        else:
            dev_out_for_prompt = dev_out_full
        prompt = (
            qa_ctx
            + _swarm_prompt_prefix(state)
            + _documentation_locale_line(state)
            + f"{langs}"
            + _project_knowledge_block(state, step_id="qa")
            + _qa_workspace_verification_instructions(state)
            + "User task:\n"
            f"{pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)}\n\n"
            f"{ca_block}"
            f"{visual_block}"
            "Development output:\n"
            f"{dev_out_for_prompt}"
            "\n\nOutput contract:\n"
            "1. Human-readable QA report with evidence.\n"
            "2. A machine-readable `<defect_report>...</defect_report>` JSON block with defects, test_scenarios, edge_cases, regression_checks.\n"
            "3. A final line `VERDICT: OK` or `VERDICT: NEEDS_WORK`.\n"
        )
        _stream_progress_emit(
            state,
            f"QA: промпт {len(prompt):,} симв. → HTTP-вызов LLM "
            f"(model={getattr(agent, 'model', '') or '?'})…",
        )
        qa_output, qa_model_out, qa_provider_out = _llm_build_agent_run(agent, prompt, state)
        if not (qa_output or "").strip():
            logger.warning(
                "qa_node: model returned empty output — retrying with compact prompt. task_id=%s",
                (state.get("task_id") or "")[:36],
            )
            _stream_progress_emit(state, "QA: empty response — retrying with compact prompt…")
            _compact_prompt = (
                "Review the dev output below and report defects.\n\n"
                f"Dev output (first {_qa_compact_limit} chars):\n{dev_out_for_prompt[:_qa_compact_limit]}\n\n"
                "Respond with:\n"
                "1. A brief QA report.\n"
                "2. A `<defect_report>` JSON block.\n"
                "3. Final line: VERDICT: OK or VERDICT: NEEDS_WORK\n"
            )
            qa_output, qa_model_out, qa_provider_out = _llm_build_agent_run(agent, _compact_prompt, state)
        if qa_output and len(qa_output) < 500:
            logger.warning(
                "QA output too short (%d chars) — retrying with tool-use instruction",
                len(qa_output),
            )
            _qa_retry_prompt = Template(
                _prompt_fragment("qa_short_retry_template")
            ).safe_substitute(base_prompt=prompt)
            qa_output, qa_model_out, qa_provider_out = _llm_build_agent_run(agent, _qa_retry_prompt, state)
        _stream_progress_emit(
            state, f"QA: готово ({len(qa_output or '')} симв.)"
        )
        report = parse_defect_report(qa_output)
        if (qa_output or "").strip():
            from backend.App.workspace.application.doc_workspace import write_step_wiki
            write_step_wiki(state, "qa", qa_output)
        _qa_result: dict[str, Any] = {
            "qa_output": qa_output,
            "qa_task_outputs": [qa_output],
            "qa_model": qa_model_out,
            "qa_provider": qa_provider_out,
            "qa_defect_report": report.to_dict(),
        }
        if output_compression_enabled() and (qa_output or "").strip():
            _qac = compress_qa_output(qa_output)
            _qa_result["qa_compressed"] = format_compressed_qa(_qac)
            logger.debug(
                "qa_node: M-9 compressed output %d chars → %d chars compact",
                _qac.char_count, len(_qa_result["qa_compressed"]),
            )
        return _qa_result
    if len(dev_task_outputs) != len(tasks):
        filler = state.get("dev_output") or ""
        dev_task_outputs = [filler] * len(tasks) if filler else [""] * len(tasks)

    def _one_qa(i: int, task: dict[str, Any]) -> tuple[int, str, str, str]:
        subtask_id = str(task.get("id") or i + 1)
        title = str(task.get("title") or f"Subtask {i + 1}")
        _stream_progress_emit(
            state,
            f"QA {i + 1}/{task_count}: «{title[:72]}» — сборка промпта и агента…",
        )
        agent = QAAgent(
            system_prompt_path_override=qa_cfg.get("prompt_path") or qa_cfg.get("prompt"),
            model_override=_cfg_model(qa_cfg),
            environment_override=qa_cfg.get("environment"),
            system_prompt_extra=_skills_extra_for_role_cfg(state, qa_cfg),
            **_remote_api_client_kwargs_for_role(state, qa_cfg),
        )
        testing_scope = (task.get("testing_scope") or "").strip()
        dev_slice_full = dev_task_outputs[i] if i < len(dev_task_outputs) else ""
        if len(dev_slice_full) > dev_out_limit:
            logger.warning(
                "qa_node subtask %d: dev_slice truncated from %d to %d chars "
                "(SWARM_QA_DEV_OUTPUT_MAX_CHARS=%d). task_id=%s",
                i + 1,
                len(dev_slice_full),
                dev_out_limit,
                dev_out_limit,
                (state.get("task_id") or "")[:36],
            )
            dev_slice = dev_slice_full[:dev_out_limit] + "\n…[dev_output truncated]"
        else:
            dev_slice = dev_slice_full
        focus = (
            testing_scope
            if testing_scope
            else (
                f"Verify the development result for subtask '{title}'; "
                "refer to the specification and Dev output below."
            )
        )
        prompt = (
            qa_ctx
            + _swarm_prompt_prefix(state)
            + _documentation_locale_line(state)
            + f"{langs}"
            + _project_knowledge_block(state, step_id="qa")
            + _qa_workspace_verification_instructions(state)
            + "[Pipeline rule] Verify compliance with the **stack (Architect)** and Dev output "
            "for this subtask.\n\n"
            "[Increment] Brief report on the **current scope**; do not run full regression of the entire spec "
            "unless explicitly required in the focus below.\n\n"
            "User task:\n"
            f"{pipeline_user_task(state) if use_mcp else build_phase_pipeline_user_context(state)}\n\n"
            f"{ca_block}"
            f"{visual_block}"
            f"## Subtask [{subtask_id}] {title}\n"
            f"Testing focus:\n{focus}\n\n"
            "Dev output for this subtask:\n"
            f"{dev_slice}"
            "\n\nOutput contract:\n"
            "1. Human-readable QA report with evidence.\n"
            "2. A machine-readable `<defect_report>...</defect_report>` JSON block.\n"
            "3. A final line `VERDICT: OK` or `VERDICT: NEEDS_WORK`.\n"
        )
        _stream_progress_emit(
            state,
            f"QA {i + 1}/{task_count}: промпт {len(prompt):,} симв. → HTTP-вызов LLM "
            f"(model={getattr(agent, 'model', '') or '?'})…",
        )
        output, used_model, used_provider = _llm_build_agent_run(agent, prompt, state)
        if not (output or "").strip():
            logger.warning(
                "qa_node subtask %d/%d: empty output — retrying with compact prompt. task_id=%s",
                i + 1, task_count, (state.get("task_id") or "")[:36],
            )
            _stream_progress_emit(
                state,
                f"QA {i + 1}/{task_count}: empty response — retrying with compact prompt…",
            )
            _compact = (
                f"Review the dev output for subtask '{title}' and report defects.\n\n"
                f"Dev output (first {_qa_compact_limit} chars):\n{dev_slice[:_qa_compact_limit]}\n\n"
                "Respond with:\n"
                "1. A brief QA report.\n"
                "2. A `<defect_report>` JSON block.\n"
                "3. Final line: VERDICT: OK or VERDICT: NEEDS_WORK\n"
            )
            output, used_model, used_provider = _llm_build_agent_run(agent, _compact, state)
        if output and len(output) < 500:
            logger.warning(
                "QA subtask %d/%d output too short (%d chars) — retrying with tool-use instruction",
                i + 1, task_count, len(output),
            )
            _qa_retry = Template(
                _prompt_fragment("qa_subtask_short_retry_template")
            ).safe_substitute(base_prompt=prompt)
            output, used_model, used_provider = _llm_build_agent_run(agent, _qa_retry, state)
        _stream_progress_emit(
            state,
            f"QA {i + 1}/{task_count}: «{title[:56]}» — готово ({len(output or '')} симв.)",
        )
        return i, output, used_model, used_provider

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.App.orchestration.application.pipeline.parallel_limits import swarm_max_parallel_tasks

    task_count = len(tasks)
    qa_task_outputs = []
    qa_model, qa_provider = "", ""

    topology = (state.get("agent_config") or {}).get("swarm", {}).get("topology", "")
    if topology == "mesh" and task_count > 1:
        max_workers = min(swarm_max_parallel_tasks(), task_count)
        results: list[Optional[tuple[int, str, str, str]]] = [None] * task_count
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fut_to_idx = {executor.submit(_one_qa, i, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(fut_to_idx):
                i = fut_to_idx[fut]
                _, qa_task_output, qa_model, qa_provider = fut.result()  # raises on thread error
                results[i] = (i, qa_task_output, qa_model, qa_provider)
        for item in results:
            if item is None:
                continue  # unreachable: all slots filled via as_completed above
            _, qa_task_output, qa_model, qa_provider = item
            qa_task_outputs.append(qa_task_output)
    else:
        for subtask_idx, task in enumerate(tasks):
            _, qa_task_output, qa_model, qa_provider = _one_qa(subtask_idx, task)
            qa_task_outputs.append(qa_task_output)

    sections: list[str] = []
    for subtask_idx, task in enumerate(tasks):
        subtask_id = str(task.get("id") or subtask_idx + 1)
        title = str(task.get("title") or f"Subtask {subtask_idx + 1}")
        task_output = qa_task_outputs[subtask_idx] if subtask_idx < len(qa_task_outputs) else ""
        sections.append(f"### QA [{subtask_id}] {title}\n\n{task_output}")
    merged = "\n\n---\n\n".join(sections)
    report = DefectReport()
    for qa_text in qa_task_outputs:
        report.merge(parse_defect_report(qa_text))
    if (merged or "").strip():
        from backend.App.workspace.application.doc_workspace import write_step_wiki
        write_step_wiki(state, "qa", merged)
    _qa_multi_result: dict[str, Any] = {
        "qa_output": merged,
        "qa_task_outputs": qa_task_outputs,
        "qa_model": qa_model,
        "qa_provider": qa_provider,
        "qa_defect_report": report.to_dict(),
    }
    if output_compression_enabled() and (merged or "").strip():
        _qac_multi = compress_qa_output(merged)
        _qa_multi_result["qa_compressed"] = format_compressed_qa(_qac_multi)
        logger.debug(
            "qa_node: M-9 compressed multi-task output %d chars → %d chars compact",
            _qac_multi.char_count, len(_qa_multi_result["qa_compressed"]),
        )
    return _qa_multi_result


def _review_dev_output_max_chars() -> int:
    env_value = os.getenv("SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 60_000


def _review_spec_max_chars() -> int:
    env_value = os.getenv("SWARM_REVIEW_SPEC_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 40_000


def _visual_evidence_prompt_block(
    state: PipelineState,
    *,
    max_chars: int = 18_000,
) -> str:
    manifest = state.get("visual_probe_manifest")
    visual_probe_output = str(state.get("visual_probe_output") or "").strip()
    visual_design_review = str(state.get("visual_design_review_output") or "").strip()

    chunks: list[str] = []
    if isinstance(manifest, dict) and manifest:
        chunks.append(
            "Visual probe manifest:\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    elif visual_probe_output:
        chunks.append("Visual probe output:\n" + visual_probe_output)
    if visual_design_review:
        chunks.append("Visual design review:\n" + visual_design_review)
    if not chunks:
        return ""

    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[visual evidence truncated]"
    return "\n[Visual runtime evidence]\n" + text + "\n\n"


def review_qa_node(state: PipelineState) -> dict[str, Any]:
    task_id = (state.get("task_id") or "")[:36]
    use_mcp = _should_use_mcp_for_workspace(state)

    dev_output_full = state.get("dev_output") or ""
    dev_limit = 4_000 if use_mcp else _review_dev_output_max_chars()
    if len(dev_output_full) > dev_limit:
        logger.warning(
            "review_qa_node: dev_output truncated from %d to %d chars "
            "(SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS=%d). task_id=%s",
            len(dev_output_full), dev_limit, dev_limit, task_id,
        )
        dev_output = dev_output_full[:dev_limit] + "\n…[dev_output truncated — increase SWARM_REVIEW_DEV_OUTPUT_MAX_CHARS to see more]"
    else:
        dev_output = dev_output_full

    qa_output_full = state.get("qa_output") or ""
    qa_limit = 3_000 if use_mcp else _review_spec_max_chars()
    if len(qa_output_full) > qa_limit:
        logger.warning(
            "review_qa_node: qa_output truncated from %d to %d chars "
            "(SWARM_REVIEW_SPEC_MAX_CHARS=%d). task_id=%s",
            len(qa_output_full), qa_limit, qa_limit, task_id,
        )
        qa_output = qa_output_full[:qa_limit] + "\n…[qa_output truncated — increase SWARM_REVIEW_SPEC_MAX_CHARS to see more]"
    else:
        qa_output = qa_output_full

    user_block = embedded_pipeline_input_for_review(state, log_node="review_qa_node")
    visual_block = _visual_evidence_prompt_block(state, max_chars=20_000)
    prompt = (
        "Step: qa (tests / report).\n"
        "Checklist — issue VERDICT: NEEDS_WORK if ANY item fails:\n"
        "[ ] Test types match the stack (unit/integration/e2e appropriate for the tech)\n"
        "[ ] Exit criteria are explicit (pass/fail conditions defined)\n"
        "[ ] Negative/edge-case scenarios are covered, not only happy paths\n"
        "[ ] Tests reference the actual Dev output (not generic stubs)\n"
        "[ ] For browser/UI tasks, visual runtime evidence exists or is explicitly skipped for a non-UI reason\n"
        "[ ] For browser/UI tasks, visual evidence has no startup failures, blank pages, console errors, network failures, or responsive overflow\n"
        "[ ] For browser/UI tasks, screenshots cover at least desktop and mobile unless the spec says otherwise\n"
        "[ ] No web E2E tests for native-mobile stack without explicit requirement\n\n"
        "Output contract:\n"
        "1. Human-readable QA review summary.\n"
        "2. A machine-readable `<defect_report>...</defect_report>` JSON block.\n"
        "3. Final line `VERDICT: OK` or `VERDICT: NEEDS_WORK`.\n\n"
        f"User task:\n{user_block}\n\n"
        f"{visual_block}"
        f"Dev artifact:\n{dev_output}\n\n"
        f"QA artifact:\n{qa_output}"
    )
    result = run_reviewer_or_moa(
        state,
        pipeline_step="review_qa",
        prompt=prompt,
        output_key="qa_review_output",
        model_key="qa_review_model",
        provider_key="qa_review_provider",
        agent_factory=lambda: _make_reviewer_agent(state),
        require_json_defect_report=True,
    )
    result["qa_review_defect_report"] = parse_defect_report(str(result.get("qa_review_output") or "")).to_dict()
    return result


def human_qa_node(state: PipelineState) -> dict[str, Any]:
    bundle = f"QA:\n{state['qa_output']}\n\nReview:\n{state['qa_review_output']}"
    agent = _make_human_agent(state, "qa")
    return {"qa_human_output": agent.run(bundle)}
