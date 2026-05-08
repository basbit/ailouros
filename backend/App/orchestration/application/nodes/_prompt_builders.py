from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
from backend.App.orchestration.domain.pipeline_machine import PipelinePhase
from backend.App.orchestration.application.agents.agent_runner import (
    run_agent_with_boundary as _canonical_run_agent_with_boundary,
)
from backend.App.orchestration.application.nodes._prompt_agent_runner import (
    run_agent_with_boundary as _delegated_run_agent_with_boundary,
    run_agent_with_optional_mcp,
    validate_agent_boundary as _delegated_validate_agent_boundary,
)
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.shared.infrastructure.app_config_load import load_app_config_json

logger = logging.getLogger(__name__)


def _current_phase(state: PipelineState) -> Optional[PipelinePhase]:
    raw = str(state.get("pipeline_phase") or "").strip().upper()
    if not raw:
        return None
    try:
        return PipelinePhase(raw)
    except ValueError:
        logger.warning("pipeline_phase=%r is not a recognised PipelinePhase", raw)
        return None


_ASSEMBLED_USER_TASK_MARKER = "\n\n---\n\n# User task\n\n"


@lru_cache(maxsize=1)
def _prompt_fragments() -> dict[str, Any]:
    return load_app_config_json("prompt_fragments.json")


def _prompt_fragment(key: str) -> str:
    value = str(_prompt_fragments().get(key) or "")
    if not value:
        raise RuntimeError(f"prompt_fragments.{key} is empty")
    return value


def _compact_workspace_note(*, key: str, workspace_root: str) -> str:
    section = _prompt_fragments().get("compact_build_workspace")
    if not isinstance(section, dict):
        raise RuntimeError("prompt_fragments.compact_build_workspace is not configured")
    template = str(section.get(key) or "")
    if not template:
        raise RuntimeError(f"prompt_fragments.compact_build_workspace.{key} is empty")
    return template.format(workspace_root=workspace_root or "(unknown)")


def _mcp_tool_call_fallback_enabled() -> bool:
    return os.getenv("SWARM_MCP_TOOL_CALL_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def pipeline_user_task(state: PipelineState) -> str:
    raw = state.get("user_task")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    inp = state.get("input") or ""
    if isinstance(inp, str) and _ASSEMBLED_USER_TASK_MARKER in inp:
        tail = inp.split(_ASSEMBLED_USER_TASK_MARKER, 1)[-1].strip()
        if tail:
            return tail
    return (inp or "").strip() if isinstance(inp, str) else ""


def task_contract_block(state: PipelineState) -> str:
    contract = state.get("task_contract")
    if not isinstance(contract, dict) or not contract:
        return ""
    return (
        "## Immutable task contract\n"
        + json.dumps(contract, ensure_ascii=False, indent=2, default=str)
    )


def _workspace_root_str(state: PipelineState) -> str:
    return str(state.get("workspace_root") or "").strip()


def _workspace_context_mode_normalized(state: PipelineState) -> str:
    context_mode = str(state.get("workspace_context_mode") or "full").strip().lower()
    return context_mode if context_mode else "full"


def _swarm_block(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    swarm_section = agent_config.get("swarm")
    return swarm_section if isinstance(swarm_section, dict) else {}


def _code_analysis_is_weak(code_analysis: dict[str, Any]) -> bool:
    if not code_analysis:
        return True
    note = str(code_analysis.get("note") or "").strip()
    if note == "workspace_root_empty":
        return True
    files = code_analysis.get("files")
    if isinstance(files, list) and len(files) == 0:
        return True
    return False


def _compact_code_analysis_for_prompt(payload: dict[str, Any], max_chars: int = 14000) -> str:
    return _compact_code_analysis_for_prompt_with_budget(payload, max_chars=max_chars)


def _compact_code_analysis_for_prompt_with_budget(
    payload: dict[str, Any],
    *,
    max_chars: int = 14_000,
    max_files: int = 120,
    relevant_paths: Optional[list[str]] = None,
) -> str:
    if not payload:
        return "{}"
    slim = dict(payload)
    files = list(slim.get("files") or [])
    if relevant_paths:
        normalized_relevant = [str(path or "").strip().lstrip("/") for path in relevant_paths if str(path or "").strip()]
        prioritized = [
            f for f in files
            if any(str(f.get("path") or "").strip().lstrip("/").startswith(path) for path in normalized_relevant)
        ]
        remainder = [f for f in files if f not in prioritized]
        files = prioritized + remainder
    slim["files"] = files[:max_files]
    json_text = json.dumps(slim, ensure_ascii=False)
    if len(json_text) > max_chars:
        return json_text[:max_chars] + "\n…[truncated]"
    return json_text


def format_conventions_for_prompt(code_analysis: dict[str, Any], max_chars: int = 0) -> str:
    if max_chars <= 0:
        max_chars = int(os.getenv("SWARM_DEV_CONVENTIONS_MAX_CHARS", "3000"))
    conventions = code_analysis.get("conventions")
    if not isinstance(conventions, dict):
        return ""
    sigs = conventions.get("example_signatures")
    if not isinstance(sigs, list) or not sigs:
        return ""
    parts: list[str] = [
        "## Existing code examples (match this style, naming, and patterns)\n",
    ]
    for sig in sigs[:5]:
        parts.append(f"```\n{sig}\n```")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text + "\n"


def find_reference_file(
    code_analysis: dict[str, Any],
    subtask_scope: str,
    workspace_root: str,
    max_chars: int = 0,
) -> str:
    if max_chars <= 0:
        max_chars = int(os.getenv("SWARM_DEV_REFERENCE_FILE_MAX_CHARS", "4000"))
    files = code_analysis.get("files")
    if not isinstance(files, list) or not files or not workspace_root:
        return ""
    scope_words = {
        w for w in re.split(r"[^a-zA-Z0-9]+", subtask_scope.lower()) if len(w) >= 3
    }
    if not scope_words:
        return ""
    best_score = 0
    best_file: dict[str, Any] = {}
    for f in files:
        if f.get("skipped") or f.get("error"):
            continue
        fpath = f.get("path", "").lower()
        entities = f.get("entities") or []
        score = 0
        for w in scope_words:
            if w in fpath:
                score += 2
            for e in entities:
                if w in e.get("name", "").lower():
                    score += 1
        score += min(len(entities), 5)
        if score > best_score:
            best_score = score
            best_file = f
    if not best_file or best_score < 3:
        return ""
    ref_path = os.path.join(workspace_root, best_file["path"])
    try:
        content = open(ref_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    if len(content) > max_chars:
        content = content[:max_chars] + "\n…[reference file truncated]"
    lang = best_file.get("language", "")
    return (
        f"## Reference file (match this style)\n"
        f"File: {best_file['path']}\n"
        f"```{lang}\n{content}\n```\n"
    )


def _review_int_env(name: str, default: int) -> int:
    env_value = os.getenv(name, "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return default


def _should_use_mcp_for_workspace(state: PipelineState) -> bool:
    from backend.App.workspace.infrastructure.workspace_io import (
        WORKSPACE_CONTEXT_MODE_RETRIEVE,
        WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
    )

    if not _workspace_root_str(state):
        return False
    mode = _workspace_context_mode_normalized(state)
    if mode not in (WORKSPACE_CONTEXT_MODE_RETRIEVE, WORKSPACE_CONTEXT_MODE_TOOLS_ONLY):
        return False
    swarm_section = _swarm_block(state)
    if swarm_section.get("skip_mcp_tools"):
        return False
    agent_config = state.get("agent_config") or {}
    _mcp_raw = agent_config.get("mcp")
    mcp_config: dict[str, Any] = _mcp_raw if isinstance(_mcp_raw, dict) else {}
    return bool(mcp_config.get("servers"))


def planning_mcp_tool_instruction(state: PipelineState) -> str:
    from backend.App.workspace.infrastructure.workspace_io import (
        WORKSPACE_CONTEXT_MODE_RETRIEVE,
        WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
    )

    if not _workspace_root_str(state):
        return ""
    mode = _workspace_context_mode_normalized(state)
    if mode not in (WORKSPACE_CONTEXT_MODE_RETRIEVE, WORKSPACE_CONTEXT_MODE_TOOLS_ONLY):
        return ""
    if _should_use_mcp_for_workspace(state):
        ws_root = _workspace_root_str(state)
        return (
            "\n\n[Workspace tools]\n"
            "The repository is not fully inlined in this message. Use the **MCP filesystem** tools "
            "available in this session (server name is often `workspace`) to list, search, and read "
            f"files under the project root: **`{ws_root}`**.\n"
            f"IMPORTANT: Always use the **full absolute path** starting with `{ws_root}/` when calling "
            "tools (e.g. `read_file`, `list_directory`). Relative paths will be rejected.\n"
        )
    if mode == WORKSPACE_CONTEXT_MODE_RETRIEVE and bool(state.get("workspace_context_mcp_fallback")):
        return (
            "\n\n[Workspace]\n"
            "MCP filesystem tools are not configured for this run (see orchestrator logs). "
            "Rely on the file index in the user message if present.\n"
        )
    return ""


def _llm_planning_agent_run(
    agent: BaseAgent,
    prompt: str,
    state: PipelineState,
    *,
    disable_tools: bool = False,
) -> tuple[str, str, str]:
    use_mcp = _should_use_mcp_for_workspace(state)
    if disable_tools:
        use_mcp = False
        logger.debug("_llm_planning_agent_run: disable_tools=True — skipping MCP tool calls")
    if use_mcp and state.get("mcp_tool_call_suspected_failure"):
        logger.info(
            "mcp_tool_call_suspected_failure flag set — switching planning step to inline context "
            "(skipping MCP tool calls)"
        )
        use_mcp = False
        code_analysis = state.get("code_analysis")
        if isinstance(code_analysis, dict) and code_analysis:
            compact = _compact_code_analysis_for_prompt(code_analysis, max_chars=10_000)
            prompt = (
                _prompt_fragment("mcp_tool_failure_inline_analysis_header")
                + compact
                + "\n\n"
                + prompt
            )
    if use_mcp:
        _planning_max_rounds_env = os.getenv("SWARM_PLANNING_MAX_TOOL_ROUNDS", "4").strip()
        try:
            _planning_max_rounds = int(_planning_max_rounds_env) if _planning_max_rounds_env else None
        except ValueError:
            _planning_max_rounds = None
        return run_agent_with_optional_mcp(
            agent, prompt, state,
            readonly_tools=True,
            max_tool_rounds=_planning_max_rounds,
        )
    effective_prompt = prompt
    if _mcp_tool_call_fallback_enabled():
        effective_prompt = _prompt_fragment("tool_call_fallback_hint") + prompt
    output = _canonical_run_agent_with_boundary(state, agent, effective_prompt)
    return output, agent.used_model, agent.used_provider


def _llm_agent_run_with_optional_mcp(
    agent: BaseAgent,
    prompt: str,
    state: PipelineState,
    *,
    readonly_tools: bool = False,
    max_tool_rounds: Optional[int] = None,
) -> tuple[str, str, str]:
    return run_agent_with_optional_mcp(
        agent, prompt, state,
        readonly_tools=readonly_tools,
        max_tool_rounds=max_tool_rounds,
    )


def _validate_agent_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
    output: str,
) -> None:
    _delegated_validate_agent_boundary(state, agent, prompt, output)


def _run_agent_with_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
) -> str:
    return _delegated_run_agent_with_boundary(state, agent, prompt)


def build_compact_build_phase_user_context(state: PipelineState) -> str:
    user_task = pipeline_user_task(state)
    project_manifest = str(state.get("project_manifest") or "").strip()
    workspace_root = _workspace_root_str(state)
    budget = _context_budget_profile(state)
    _ca_raw = state.get("code_analysis")
    code_analysis_data: dict[str, Any] = _ca_raw if isinstance(_ca_raw, dict) else {}
    ca_txt = _compact_code_analysis_for_prompt_with_budget(
        code_analysis_data,
        max_chars=budget["code_analysis_max_chars"],
        max_files=budget["code_analysis_max_files"],
        relevant_paths=_relevant_context_paths(state),
    )
    if _should_use_mcp_for_workspace(state) and not bool(
        state.get("mcp_tool_call_suspected_failure")
    ):
        mcp_note = _compact_workspace_note(
            key="mcp_available",
            workspace_root=workspace_root,
        )
    else:
        mcp_note = _compact_workspace_note(
            key="mcp_unavailable",
            workspace_root=workspace_root,
        )
    parts: list[str] = []
    if project_manifest:
        parts.append("# Project context (canonical)\n\n" + project_manifest)
    contract = task_contract_block(state)
    if contract:
        parts.append(contract)
    fix_cycle_summary = _fix_cycle_context_summary(state, max_chars=budget["fix_cycle_summary_max_chars"])
    if fix_cycle_summary:
        parts.append(fix_cycle_summary)
    parts.append("## Static code analysis (compact JSON)\n" + ca_txt + mcp_note.strip())
    body = "\n\n---\n\n".join(parts)
    return body + "\n\n---\n\n# User task\n\n" + user_task + "\n"


def should_use_compact_build_pipeline_input(state: PipelineState) -> bool:
    from backend.App.workspace.infrastructure.workspace_io import (
        WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT,
        WORKSPACE_CONTEXT_MODE_RETRIEVE,
        WORKSPACE_CONTEXT_MODE_TOOLS_ONLY,
    )

    mode = _workspace_context_mode_normalized(state)
    current_step_id = str(state.get("_current_step_id") or "").strip()
    if (
        current_step_id in {"dev", "qa", "devops"}
        and _current_phase(state) is PipelinePhase.FIX
        and bool(state.get("open_defects"))
    ):
        return True
    if mode in (WORKSPACE_CONTEXT_MODE_TOOLS_ONLY, WORKSPACE_CONTEXT_MODE_RETRIEVE):
        return bool(_workspace_root_str(state))
    if mode != WORKSPACE_CONTEXT_MODE_POST_ANALYSIS_COMPACT:
        return False
    _ca_raw2 = state.get("code_analysis")
    code_analysis: dict[str, Any] = _ca_raw2 if isinstance(_ca_raw2, dict) else {}
    if _code_analysis_is_weak(code_analysis):
        logger.warning(
            "post_analysis_compact: code_analysis weak — build steps keep full pipeline input. task_id=%s",
            (state.get("task_id") or "")[:36],
        )
        return False
    return True


def _relevant_context_paths(state: PipelineState) -> list[str]:
    relevant: list[str] = []

    def _add(raw: Any) -> None:
        text = str(raw or "").strip().lstrip("/")
        if text and text not in relevant:
            relevant.append(text)

    for key in ("must_exist_files", "production_paths"):
        _items = state.get(key) or []
        if isinstance(_items, list):
            for item in _items:
                _add(item)
    _ww_raw = state.get("workspace_writes")
    workspace_writes: dict[str, Any] = _ww_raw if isinstance(_ww_raw, dict) else {}
    for key in ("written", "patched", "udiff_applied"):
        for item in workspace_writes.get(key, []) or []:
            _add(item)
    for defect in state.get("open_defects") or []:
        if isinstance(defect, dict):
            for path in defect.get("file_paths") or []:
                _add(path)
    return relevant


_CODE_ANALYSIS_BUDGET_FIELDS: dict[str, str] = {
    "max_chars":                 "code_analysis_chars",
    "max_files":                 "code_analysis_max_files",
    "fix_cycle_summary_max_chars": "fix_cycle_summary_chars",
}


def _code_analysis_legacy_env(state: PipelineState, legacy_field: str) -> Optional[int]:
    step = str(state.get("_current_step_id") or "").strip().upper()
    phase = str(state.get("pipeline_phase") or "").strip().upper()
    field_upper = legacy_field.upper()

    candidates: list[str] = []
    if step and phase:
        candidates.append(f"SWARM_CODE_ANALYSIS_{field_upper}_{step}_{phase}")
    if step:
        candidates.append(f"SWARM_CODE_ANALYSIS_{field_upper}_{step}")
    if phase:
        candidates.append(f"SWARM_CODE_ANALYSIS_{field_upper}_{phase}")
    candidates.append(f"SWARM_CODE_ANALYSIS_{field_upper}")

    for env_var in candidates:
        raw = os.environ.get(env_var, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid {env_var}={raw!r}: expected int. {exc}"
                ) from exc
    return None


def _context_budget_profile(state: PipelineState) -> dict[str, int]:
    from backend.App.orchestration.application.context.context_budget import (
        get_context_budget,
    )

    step_id = str(state.get("_current_step_id") or "").strip()
    agent_config = state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None
    budget = get_context_budget(step_id, agent_config)

    result: dict[str, int] = {
        "code_analysis_max_chars":      budget.code_analysis_chars,
        "code_analysis_max_files":      budget.code_analysis_max_files,
        "fix_cycle_summary_max_chars":  budget.fix_cycle_summary_chars,
    }
    for legacy_field, _budget_field in _CODE_ANALYSIS_BUDGET_FIELDS.items():
        env_val = _code_analysis_legacy_env(state, legacy_field)
        if env_val is not None:
            if legacy_field == "max_chars":
                result["code_analysis_max_chars"] = env_val
            elif legacy_field == "max_files":
                result["code_analysis_max_files"] = env_val
            elif legacy_field == "fix_cycle_summary_max_chars":
                result["fix_cycle_summary_max_chars"] = env_val
    return result


def _fix_cycle_context_summary(state: PipelineState, *, max_chars: int) -> str:
    if _current_phase(state) is not PipelinePhase.FIX:
        return ""
    parts: list[str] = ["# Fix cycle context reset"]
    clustered = state.get("clustered_open_defects") or []
    if clustered:
        lines = ["Open defect clusters:"]
        for cluster in clustered[:8]:
            if not isinstance(cluster, dict):
                continue
            title = str(cluster.get("cluster_key") or cluster.get("category") or "uncategorized")
            count = int(cluster.get("count") or 0)
            files = ", ".join((cluster.get("file_paths") or [])[:3])
            lines.append(f"- {title}: {count} defect(s){f' in {files}' if files else ''}")
        parts.append("\n".join(lines))
    verification_gates = state.get("verification_gates") or []
    failed_gates = [
        str(item.get("gate_name") or "")
        for item in verification_gates
        if isinstance(item, dict) and not item.get("passed", False)
    ]
    if failed_gates:
        parts.append("Failed trusted checks: " + ", ".join(failed_gates[:6]))
    step_retries = state.get("step_retries") if isinstance(state.get("step_retries"), dict) else {}
    if step_retries:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(step_retries.items()) if int(value or 0) > 0)
        if summary:
            parts.append("Retry counters: " + summary)
    step_feedback = state.get("step_feedback") if isinstance(state.get("step_feedback"), dict) else {}
    if step_feedback:
        snippets: list[str] = []
        for step_id, items in step_feedback.items():
            if not isinstance(items, list) or not items:
                continue
            last = str(items[-1] or "").strip().replace("\n", " ")
            if last:
                snippets.append(f"- {step_id}: {last[:180]}")
        if snippets:
            parts.append("Recent failed-attempt notes:\n" + "\n".join(snippets[:6]))
    text = "\n\n".join(parts).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…[fix-cycle summary truncated]"


def build_phase_pipeline_user_context(state: PipelineState) -> str:
    if should_use_compact_build_pipeline_input(state):
        return build_compact_build_phase_user_context(state)
    return state.get("input") or ""


def planning_pipeline_user_context(state: PipelineState) -> str:
    user_input = state.get("input") or ""
    source_research = str(state.get("source_research_output") or "").strip()
    wiki_ctx = (state.get("wiki_context") or "").strip()
    parts: list[str] = []
    if source_research and source_research != "SOURCE_RESEARCH_NOT_REQUIRED":
        if len(source_research) > 6000:
            source_research = source_research[:6000] + "\n…[source research truncated]"
        parts.append("[External source research brief]\n" + source_research)
    if wiki_ctx:
        parts.append(f"[Project wiki memory]\n{wiki_ctx}")
    contract = task_contract_block(state)
    if contract:
        parts.append(contract)
    if user_input:
        parts.append(user_input)
    return "\n\n".join(parts)


def _should_compact_for_reviewer(log_node: str, state: PipelineState) -> bool:
    if not should_use_compact_build_pipeline_input(state):
        return False
    review_node_prefixes = ("review_devops", "review_dev_lead", "review_dev", "review_qa")
    return any(prefix in log_node for prefix in review_node_prefixes)


def embedded_pipeline_input_for_review(state: PipelineState, *, log_node: str) -> str:
    from backend.App.orchestration.application.nodes._prompt_review_block import (
        render_embedded_pipeline_input_for_review,
    )
    return render_embedded_pipeline_input_for_review(
        state,
        log_node=log_node,
        user_task_provider=pipeline_user_task,
        compact_input_provider=build_phase_pipeline_user_context,
        should_use_mcp=_should_use_mcp_for_workspace(state),
        should_compact_for_reviewer=_should_compact_for_reviewer(log_node, state),
    )


def embedded_review_artifact(
    state: PipelineState,
    text: Any,
    *,
    log_node: str,
    part_name: str,
    env_name: str,
    default_max: int,
    mcp_max: Optional[int] = None,
) -> str:
    from backend.App.orchestration.application.nodes._prompt_review_block import (
        render_embedded_review_artifact,
    )
    return render_embedded_review_artifact(
        state,
        text,
        log_node=log_node,
        part_name=part_name,
        env_name=env_name,
        default_max=default_max,
        mcp_max=mcp_max,
        should_use_mcp=_should_use_mcp_for_workspace(state),
    )


_SPEC_FOR_BUILD_MAX_CHARS = int(os.getenv("SWARM_SPEC_FOR_BUILD_MAX_CHARS", "60000"))

_SPEC_SUMMARY_MAX_CHARS = int(os.getenv("SWARM_SPEC_SUMMARY_MAX_CHARS", "5000"))


def spec_summary_for_subtask(
    full_spec: str,
    development_scope: str,
    *,
    max_chars: int = 0,
) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        spec_summary_for_subtask as _delegate,
    )
    return _delegate(full_spec, development_scope, max_chars=max_chars)


def _effective_spec_for_build(state: PipelineState) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        effective_spec_for_build,
    )
    return effective_spec_for_build(state)


def _spec_for_build_mcp_safe(state: PipelineState) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        spec_for_build_mcp_safe,
    )
    return spec_for_build_mcp_safe(
        state, mcp_active=_should_use_mcp_for_workspace(state),
    )


def _spec_arch_context_for_docs(state: PipelineState, max_each: int = 12000) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        spec_arch_context_for_docs,
    )
    return spec_arch_context_for_docs(state, max_each=max_each)


def _doc_spec_max_each_chars() -> int:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        doc_spec_max_each_chars,
    )
    return doc_spec_max_each_chars()


def _doc_chain_spec_max_chars() -> int:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        doc_chain_spec_max_chars,
    )
    return doc_chain_spec_max_chars()


def _doc_generate_second_pass_analysis_max_chars() -> int:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        doc_generate_second_pass_analysis_max_chars,
    )
    return doc_generate_second_pass_analysis_max_chars()


def _effective_spec_block_for_doc_chain(
    state: PipelineState,
    *,
    log_node: str,
) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        effective_spec_block_for_doc_chain,
    )
    return effective_spec_block_for_doc_chain(state, log_node=log_node)


def _documentation_product_context_block(state: PipelineState, *, log_node: str) -> str:
    from backend.App.orchestration.application.nodes._prompt_spec_helpers import (
        documentation_product_context_block,
    )
    return documentation_product_context_block(state, log_node=log_node)


def _context_budget(
    step_id: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from backend.App.orchestration.application.context.context_budget import (
        context_budget_as_dict,
        get_context_budget,
    )

    return context_budget_as_dict(get_context_budget(step_id, agent_config))


def _pipeline_context_block(state: PipelineState, current_step_id: str) -> str:
    from backend.App.orchestration.application.nodes._prompt_context_block import (
        render_pipeline_context_block,
    )
    return render_pipeline_context_block(state, current_step_id)


def _project_knowledge_block(
    state: PipelineState,
    *,
    max_chars: int = 2500,
    step_id: Optional[str] = None,
) -> str:
    from backend.App.orchestration.application.nodes._prompt_context_block import (
        render_project_knowledge_block,
    )
    return render_project_knowledge_block(state, max_chars=max_chars, step_id=step_id)


def _dev_sibling_tasks_block(all_tasks: list[dict], current_index: int) -> str:
    from backend.App.orchestration.application.nodes._prompt_context_block import (
        render_dev_sibling_tasks_block,
    )
    return render_dev_sibling_tasks_block(all_tasks, current_index)
