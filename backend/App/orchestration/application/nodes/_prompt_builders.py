"""Prompt builders and pipeline context helpers for pipeline node modules.

Extracted from _shared.py: build_*_context, planning helpers, MCP tool helpers,
_pipeline_context_block, spec/doc helpers.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Optional, cast

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent
from backend.App.orchestration.domain.exceptions import PipelineCancelled
from backend.App.orchestration.domain.pipeline_machine import PipelinePhase
from backend.App.orchestration.application.agent_runner import (
    run_agent_with_boundary as _canonical_run_agent_with_boundary,
    validate_agent_boundary as _canonical_validate_agent_boundary,
)
from backend.App.orchestration.application.pipeline_state import PipelineState

logger = logging.getLogger(__name__)


def _current_phase(state: PipelineState) -> Optional[PipelinePhase]:
    """Parse pipeline_phase from state into the typed enum.

    Returns ``None`` when the field is empty or unrecognised.  Callers must
    treat ``None`` as a domain signal (no active phase), not a default.
    """
    raw = str(state.get("pipeline_phase") or "").strip().upper()
    if not raw:
        return None
    try:
        return PipelinePhase(raw)
    except ValueError:
        logger.warning("pipeline_phase=%r is not a recognised PipelinePhase", raw)
        return None


_ASSEMBLED_USER_TASK_MARKER = "\n\n---\n\n# User task\n\n"

_TOOL_CALL_FALLBACK_HINT = (
    "[System] You do NOT have access to filesystem tools in this run. "
    "Do NOT write phrases like 'Let me check...' or 'I will analyze files...'. "
    "Use ONLY the context provided in this prompt to produce your output. "
    "If context is insufficient — state what is missing and draft the plan based on available info.\n\n"
)


def _mcp_tool_call_fallback_enabled() -> bool:
    return os.getenv("SWARM_MCP_TOOL_CALL_FALLBACK", "1").strip().lower() not in ("0", "false", "no", "off")


def pipeline_user_task(state: PipelineState) -> str:
    """Текст задачи пользователя: явное поле state или хвост собранного ``input``."""
    raw = state.get("user_task")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    inp = state.get("input") or ""
    if isinstance(inp, str) and _ASSEMBLED_USER_TASK_MARKER in inp:
        tail = inp.split(_ASSEMBLED_USER_TASK_MARKER, 1)[-1].strip()
        if tail:
            return tail
    return (inp or "").strip() if isinstance(inp, str) else ""


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
    """No files / explicit scan skip — LLM should not see only '{}' without spec context."""
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
    """Format real code examples from the project as a prompt block for dev.

    No hardcoded framework/language detection — the model determines
    conventions from the actual code snippets.
    """
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
    """Find the most relevant existing file as a reference for dev.

    Uses word overlap between subtask scope and file paths/entity names —
    no hardcoded keyword lists.
    """
    if max_chars <= 0:
        max_chars = int(os.getenv("SWARM_DEV_REFERENCE_FILE_MAX_CHARS", "4000"))
    files = code_analysis.get("files")
    if not isinstance(files, list) or not files or not workspace_root:
        return ""
    # Extract significant words from subtask scope (3+ chars, lowercased)
    scope_words = {
        w for w in re.split(r"[^a-zA-Z0-9]+", subtask_scope.lower()) if len(w) >= 3
    }
    if not scope_words:
        return ""
    # Score each file by word overlap in path + entity names
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
        # Prefer files with more entities (richer examples)
        score += min(len(entities), 5)
        if score > best_score:
            best_score = score
            best_file = f
    if not best_file or best_score < 3:
        return ""
    # Read the file from disk
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
    """MCP tool loop для шагов в режиме retrieve/tools_only при наличии servers в agent_config."""
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
    """Короткая подсказка: читать файлы через MCP, если режим retrieve/tools_only."""
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
    """Run a planning agent, optionally with MCP tool calls.

    disable_tools=True forces a tool-free direct call regardless of MCP config.
    Used for steps like clarify_input where tool calls add noise without benefit.
    """
    use_mcp = _should_use_mcp_for_workspace(state)
    if disable_tools:
        use_mcp = False
        logger.debug("_llm_planning_agent_run: disable_tools=True — skipping MCP tool calls")
    # Step 1.4: if previous planning step suspected MCP failure, force inline context
    if use_mcp and state.get("mcp_tool_call_suspected_failure"):
        logger.info(
            "mcp_tool_call_suspected_failure flag set — switching planning step to inline context "
            "(skipping MCP tool calls)"
        )
        use_mcp = False
        # Prepend compact code analysis as inline context
        code_analysis = state.get("code_analysis")
        if isinstance(code_analysis, dict) and code_analysis:
            compact = _compact_code_analysis_for_prompt(code_analysis, max_chars=10_000)
            prompt = (
                "\n\n[Workspace — inline static analysis (MCP skipped due to suspected tool_call failure)]\n"
                + compact
                + "\n\n"
                + prompt
            )
    if use_mcp:
        # Planning roles never write files — use read-only tools to reduce
        # tool-schema token overhead for small-context local models.
        _planning_max_rounds_env = os.getenv("SWARM_PLANNING_MAX_TOOL_ROUNDS", "4").strip()
        try:
            _planning_max_rounds = int(_planning_max_rounds_env) if _planning_max_rounds_env else None
        except ValueError:
            _planning_max_rounds = None
        return _llm_agent_run_with_optional_mcp(
            agent, prompt, state,
            readonly_tools=True,
            max_tool_rounds=_planning_max_rounds,
        )
    effective_prompt = prompt
    if _mcp_tool_call_fallback_enabled():
        effective_prompt = _TOOL_CALL_FALLBACK_HINT + prompt
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
    """agent.run или цикл MCP tool_calls (OpenAI-compatible), если задан ``agent_config.mcp``.

    readonly_tools: pass True for planning roles (PM/BA/Arch) — exposes only
        read/list/search tools, cutting schema token overhead for small-context models.
    """
    agent_config = state.get("agent_config") or {}
    swarm_section = _swarm_block(state)
    if swarm_section.get("skip_mcp_tools"):
        output = _canonical_run_agent_with_boundary(state, agent, prompt)
        return output, agent.used_model, agent.used_provider
    mcp = agent_config.get("mcp")
    cancel_ev: Optional[threading.Event] = cast(Optional[threading.Event], state.get("_pipeline_cancel_event"))
    if isinstance(mcp, dict) and mcp.get("servers"):
        try:
            from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
                _mcp_fallback_allow,
                run_with_mcp_tools_openai_compat,
            )
            output, used_model, used_provider = run_with_mcp_tools_openai_compat(
                system_prompt=agent.effective_system_prompt(),
                user_content=prompt,
                model=agent.model,
                environment=agent.environment,
                remote_provider=agent.remote_provider,
                remote_api_key=agent.remote_api_key,
                remote_base_url=agent.remote_base_url,
                mcp_cfg=mcp,
                cancel_event=cancel_ev,
                readonly_tools=readonly_tools,
                **({"max_rounds": max_tool_rounds} if max_tool_rounds is not None else {}),
            )
            _canonical_validate_agent_boundary(state, agent, prompt, output)
            return output, used_model, used_provider
        except PipelineCancelled:
            raise
        except Exception as exc:
            _exc_str = str(exc).lower()
            # Anthropic SDK doesn't support OpenAI-compat tool loop — expected,
            # fall through silently to plain agent.run (no env var needed).
            if "anthropic sdk is not supported" in _exc_str:
                logger.info(
                    "MCP: Anthropic SDK detected for role=%s — skipping MCP tool loop, "
                    "using plain agent.run instead.",
                    agent.role,
                )
            else:
                _is_ctx_overflow = (
                    "tokens to keep" in _exc_str
                    or ("context" in _exc_str and "length" in _exc_str)
                    or "channel error" in _exc_str
                    or "model has crashed" in _exc_str
                )
                # Auto-retry with remote profile if local model failed on context
                if _is_ctx_overflow and agent.remote_provider and agent.remote_api_key:
                    logger.warning(
                        "MCP: local model failed (role=%s model=%s) — retrying with remote "
                        "profile (provider=%s). Error: %s",
                        agent.role, agent.model, agent.remote_provider, exc,
                    )
                    try:
                        return run_with_mcp_tools_openai_compat(
                            system_prompt=agent.effective_system_prompt(),
                            user_content=prompt,
                            model=agent.model,
                            environment="cloud",
                            remote_provider=agent.remote_provider,
                            remote_api_key=agent.remote_api_key,
                            remote_base_url=agent.remote_base_url,
                            mcp_cfg=mcp,
                            cancel_event=cancel_ev,
                            readonly_tools=readonly_tools,
                            **({"max_rounds": max_tool_rounds} if max_tool_rounds is not None else {}),
                        )
                    except Exception as remote_exc:
                        logger.error(
                            "MCP: remote retry also failed (role=%s provider=%s): %s",
                            agent.role, agent.remote_provider, remote_exc,
                        )
                        raise remote_exc from exc

                from backend.App.integrations.infrastructure.mcp.openai_loop.loop import _mcp_fallback_allow
                if not _mcp_fallback_allow():
                    logger.error(
                        "MCP tool-call loop failed for role=%s. "
                        "Set SWARM_MCP_FALLBACK_ALLOW=1 to allow plain agent.run fallback. "
                        "Error: %s",
                        agent.role,
                        exc,
                        exc_info=True,
                    )
                    raise
                logger.warning(
                    "MCP tool-call loop failed for role=%s; "
                    "SWARM_MCP_FALLBACK_ALLOW=1 — продолжаем без инструментов. "
                    "Error: %s",
                    agent.role,
                    exc,
                    exc_info=True,
                )
    output = _canonical_run_agent_with_boundary(state, agent, prompt)
    if not output or not output.strip():
        logger.warning("agent.run returned empty output for role=%s model=%s", agent.role, agent.model)
    return output, agent.used_model, agent.used_provider


def _validate_agent_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
    output: str,
) -> None:
    """Backward-compatible alias for the canonical boundary validator."""
    _canonical_validate_agent_boundary(state, agent, prompt, output)


def _run_agent_with_boundary(
    state: PipelineState,
    agent: BaseAgent,
    prompt: str,
) -> str:
    """Backward-compatible alias for the canonical boundary runner."""
    return _canonical_run_agent_with_boundary(state, agent, prompt)


def build_compact_build_phase_user_context(state: PipelineState) -> str:
    """Манифест + компактный code_analysis + корень + user_task (без полного snapshot файлов)."""
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
    mcp_note = (
        f"\n\n[Workspace]\nProject root on orchestrator host: `{workspace_root or '(unknown)'}`.\n"
        "File bodies are not inlined in this block. Use **MCP workspace** filesystem tools "
        "to read or search files as needed.\n"
    )
    parts: list[str] = []
    if project_manifest:
        parts.append("# Project context (canonical)\n\n" + project_manifest)
    fix_cycle_summary = _fix_cycle_context_summary(state, max_chars=budget["fix_cycle_summary_max_chars"])
    if fix_cycle_summary:
        parts.append(fix_cycle_summary)
    parts.append("## Static code analysis (compact JSON)\n" + ca_txt + mcp_note.strip())
    body = "\n\n---\n\n".join(parts)
    return body + "\n\n---\n\n# User task\n\n" + user_task + "\n"


def should_use_compact_build_pipeline_input(state: PipelineState) -> bool:
    """Компактный user-блок для dev/qa/devops (и согласованных review_*), не для PM/BA."""
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


# ---------------------------------------------------------------------------
# Code-analysis context budget — config-driven, no hardcoded role/phase logic
# ---------------------------------------------------------------------------
#
# Resolution order for each field:
#   1. SWARM_CODE_ANALYSIS_<FIELD>_<STEP>_<PHASE>   (most specific)
#   2. SWARM_CODE_ANALYSIS_<FIELD>_<STEP>
#   3. SWARM_CODE_ANALYSIS_<FIELD>_<PHASE>
#   4. SWARM_CODE_ANALYSIS_<FIELD>                  (global default)
#   5. Module-level fallback constant (full context)
#
# This removes the hardcoded "if step == qa elif step == devops" chain
# (§3 forbids workflow logic in code).  Operators set env vars or
# agent_config to express their policy; the code layer is dumb.

# Legacy env vars kept for operators that already rely on this knob style.
# They take precedence over the per-step ContextBudget defaults so existing
# deployments keep their tuning. New deployments should prefer
# ``SWARM_CONTEXT_CODE_ANALYSIS_CHARS_<STEP>`` etc.
_CODE_ANALYSIS_BUDGET_FIELDS: dict[str, str] = {
    # legacy field name → ContextBudget field name
    "max_chars":                 "code_analysis_chars",
    "max_files":                 "code_analysis_max_files",
    "fix_cycle_summary_max_chars": "fix_cycle_summary_chars",
}


def _code_analysis_legacy_env(state: PipelineState, legacy_field: str) -> Optional[int]:
    """Resolve a code-analysis budget field via the legacy env var cascade.

    Returns ``None`` if no env var is set; the caller then falls back
    to the per-step :class:`ContextBudget`. No role/phase knowledge in
    code — this only consults env vars supplied by ops.
    """
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
    """Return code-analysis budget for the current step/phase.

    Defaults come from the per-step :class:`ContextBudget` (resolved via
    :func:`get_context_budget`). Legacy ``SWARM_CODE_ANALYSIS_*`` env
    vars override that default — order matches the wider H-1 design:
    env wins over agent_config wins over role profile.
    """
    from backend.App.orchestration.application.context_budget import (
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
            # legacy "max_chars" → result key "code_analysis_max_chars" etc.
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
    """Контекст для clarify/PM/BA/Arch/спека — как собрал оркестратор (см. prepare_workspace)."""
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
    if user_input:
        parts.append(user_input)
    return "\n\n".join(parts)


def _should_compact_for_reviewer(log_node: str, state: PipelineState) -> bool:
    if not should_use_compact_build_pipeline_input(state):
        return False
    review_node_prefixes = ("review_devops", "review_dev_lead", "review_dev", "review_qa")
    return any(prefix in log_node for prefix in review_node_prefixes)


def embedded_pipeline_input_for_review(state: PipelineState, *, log_node: str) -> str:
    """Cap ``state['input']`` (workspace snapshot + user task) for reviewer prompts.

    When MCP is active (small-context local model) only the user task text is
    returned — the workspace snapshot does not fit in a 4 K-token context.
    """
    if _should_use_mcp_for_workspace(state):
        # Small-context model: reviewer only needs the task description.
        return pipeline_user_task(state)
    if _should_compact_for_reviewer(log_node, state):
        pipeline_input = build_phase_pipeline_user_context(state)
    else:
        pipeline_input = state.get("input") or ""
    if not isinstance(pipeline_input, str):
        pipeline_input = str(pipeline_input)
    max_chars = _review_int_env("SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS", 100_000)
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(pipeline_input) <= max_chars:
        return pipeline_input
    logger.warning(
        "%s: pipeline input truncated from %d to %d chars "
        "(SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS=%d). task_id=%s",
        log_node,
        len(pipeline_input),
        max_chars,
        max_chars,
        task_id_prefix,
    )
    return (
        pipeline_input[:max_chars]
        + "\n…[pipeline input truncated — increase SWARM_REVIEW_PIPELINE_INPUT_MAX_CHARS]"
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
    """Cap one large block (PM/BA/Arch/spec/…) inside a reviewer prompt.

    mcp_max: when set and MCP is active (small-context local model), this cap
    is used instead of default_max (unless the env var overrides it).

    Warns when the artifact is empty so reviewers don't silently assess nothing
    and produce hallucinated verdicts (artifact_path_exists guard).
    """
    full_text = text if isinstance(text, str) else str(text or "")
    if not full_text.strip():
        logger.warning(
            "%s: %s artifact is EMPTY — reviewer will assess a blank artifact "
            "(artifact_path_exists=False). Step output was not produced or not stored "
            "in pipeline state. Check previous pipeline steps for failures.",
            log_node,
            part_name,
        )
    if mcp_max is not None and _should_use_mcp_for_workspace(state):
        effective_default = mcp_max
    else:
        effective_default = default_max
    max_chars = _review_int_env(env_name, effective_default)
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(full_text) <= max_chars:
        return full_text
    logger.warning(
        "%s: %s truncated from %d to %d chars (%s=%d). task_id=%s",
        log_node,
        part_name,
        len(full_text),
        max_chars,
        env_name,
        max_chars,
        task_id_prefix,
    )
    return full_text[:max_chars] + f"\n…[truncated — increase {env_name}]"


_SPEC_FOR_BUILD_MAX_CHARS = int(os.getenv("SWARM_SPEC_FOR_BUILD_MAX_CHARS", "60000"))

_SPEC_SUMMARY_MAX_CHARS = int(os.getenv("SWARM_SPEC_SUMMARY_MAX_CHARS", "5000"))


def spec_summary_for_subtask(
    full_spec: str,
    development_scope: str,
    *,
    max_chars: int = 0,
) -> str:
    """Build a compact spec context for a Dev subtask.

    This is a **deterministic, content-agnostic** reducer: it truncates the
    spec to ``max_chars`` characters and appends the subtask's
    ``development_scope`` verbatim.  It does NOT parse, search, or make any
    assumption about spec structure — the spec is opaque text (§1, §3,
    §8 of docs/review-rules.md).

    If callers need a semantic summary, they must compute it upstream
    (e.g. the Architect agent can emit a dedicated ``spec_summary`` field
    that is passed in here as ``full_spec``).

    Inputs:
      ``full_spec``         — spec text (already truncated upstream)
      ``development_scope`` — subtask's own scope block from Dev Lead plan
      ``max_chars``         — hard character cap (0 → ``SWARM_SPEC_SUMMARY_MAX_CHARS``)
    """
    if max_chars < 0:
        raise ValueError(f"max_chars must be non-negative, got {max_chars}")
    if max_chars == 0:
        max_chars = _SPEC_SUMMARY_MAX_CHARS

    spec_text = full_spec.strip()
    scope_text = development_scope.strip()

    if not spec_text and not scope_text:
        return ""

    parts: list[str] = []
    if spec_text:
        truncated = spec_text[:max_chars]
        marker = "" if len(spec_text) <= max_chars else "\n…[spec truncated to max_chars]"
        parts.append("[Approved specification — context for subtask]")
        parts.append(truncated + marker)
    if scope_text:
        parts.append("\n[Subtask development scope]")
        parts.append(scope_text)

    return "\n".join(parts)


def _effective_spec_for_build(state: PipelineState) -> str:
    """Spec for Dev/DevOps/Dev Lead: ``spec_merge`` if present, otherwise accumulated step outputs."""
    spec = (state.get("spec_output") or "").strip()
    if spec:
        if len(spec) > _SPEC_FOR_BUILD_MAX_CHARS:
            logger.warning(
                "pipeline build: spec_output truncated from %d to %d chars (SWARM_SPEC_FOR_BUILD_MAX_CHARS)",
                len(spec), _SPEC_FOR_BUILD_MAX_CHARS,
            )
            spec = spec[:_SPEC_FOR_BUILD_MAX_CHARS] + "\n…[spec truncated]"
        return spec
    pm_output = (state.get("pm_output") or "").strip()
    ba = (state.get("ba_output") or "").strip()
    arch = (state.get("arch_output") or "").strip()
    # Cap individual parts to avoid unbounded concatenation
    _part_cap = _SPEC_FOR_BUILD_MAX_CHARS // 3
    parts: list[str] = []
    if pm_output:
        parts.append("[PM — plan and tasks]\n" + pm_output[:_part_cap])
    if ba:
        parts.append("[BA — requirements]\n" + ba[:_part_cap])
    if arch:
        parts.append("[Architect — stack and boundaries]\n" + arch[:_part_cap])
    if parts:
        merged = "\n\n---\n\n".join(parts)
        logger.info(
            "pipeline build: spec_output пуст; для build-шагов используется контекст PM/BA/Architect "
            "(%d симв.). Для одной утверждённой спеки добавь шаг spec_merge (task_id=%s)",
            len(merged),
            (state.get("task_id") or "")[:36],
        )
        return merged
    logger.warning(
        "pipeline build: spec_output пуст и нет pm_output/ba_output/arch_output — "
        "Dev/DevOps/Dev Lead без спеки (добавь шаги или spec_merge; task_id=%s)",
        (state.get("task_id") or "")[:36],
    )
    return ""


def _spec_for_build_mcp_safe(state: PipelineState) -> str:
    """Return build-phase spec, truncated to SWARM_MCP_SPEC_MAX_CHARS when MCP is active.

    On small-context local models (phi-4, 4K context) the full spec (PM+BA+Arch outputs)
    can be 5000–20000 chars and overflow the context window before any tool calls execute.
    The model can read additional details via MCP filesystem tools.
    """
    spec = _effective_spec_for_build(state)
    if not _should_use_mcp_for_workspace(state):
        return spec
    env_value = os.getenv("SWARM_MCP_SPEC_MAX_CHARS", "").strip()
    max_chars = int(env_value) if env_value.isdigit() and int(env_value) > 0 else 3000
    if len(spec) <= max_chars:
        return spec
    logger.warning(
        "MCP build: spec truncated from %d to %d chars (SWARM_MCP_SPEC_MAX_CHARS=%d). "
        "Increase SWARM_MCP_SPEC_MAX_CHARS or raise model n_ctx.",
        len(spec), max_chars, max_chars,
    )
    return spec[:max_chars] + f"\n…[spec truncated — set SWARM_MCP_SPEC_MAX_CHARS to increase (current: {max_chars})]"


def _spec_arch_context_for_docs(state: PipelineState, max_each: int = 12000) -> str:
    spec = (state.get("spec_output") or "").strip()
    arch = (state.get("arch_output") or "").strip()
    parts: list[str] = []
    if spec:
        parts.append("[Approved specification (spec_merge)]\n" + spec[:max_each])
    if arch:
        parts.append("[Architect output (pipeline)]\n" + arch[:max_each])
    return "\n\n".join(parts) if parts else ""


def _doc_spec_max_each_chars() -> int:
    return _review_int_env("SWARM_DOC_SPEC_MAX_CHARS", 12_000)


def _doc_chain_spec_max_chars() -> int:
    return _review_int_env("SWARM_DOC_CHAIN_SPEC_MAX_CHARS", 24_000)


def _doc_generate_second_pass_analysis_max_chars() -> int:
    return _review_int_env("SWARM_DOCUMENTATION_DOC_PASS_MAX_ANALYSIS_CHARS", 9000)


def _effective_spec_block_for_doc_chain(
    state: PipelineState,
    *,
    log_node: str,
) -> str:
    """Capped ``_effective_spec_for_build`` for problem_spotter / refactor_plan / doc fallback."""
    full_spec = _effective_spec_for_build(state).strip()
    if not full_spec:
        return ""
    max_chars = _doc_chain_spec_max_chars()
    task_id_prefix = (state.get("task_id") or "")[:36]
    if len(full_spec) <= max_chars:
        return full_spec
    logger.warning(
        "%s: effective spec for doc chain truncated from %d to %d chars "
        "(SWARM_DOC_CHAIN_SPEC_MAX_CHARS=%d). task_id=%s",
        log_node,
        len(full_spec),
        max_chars,
        max_chars,
        task_id_prefix,
    )
    return full_spec[:max_chars] + "\n…[truncated — increase SWARM_DOC_CHAIN_SPEC_MAX_CHARS]"


def _documentation_product_context_block(state: PipelineState, *, log_node: str) -> str:
    """Prefer spec_merge + architect slices; if both empty, use capped PM/BA/Arch merge."""
    max_each = _doc_spec_max_each_chars()
    sa = _spec_arch_context_for_docs(state, max_each=max_each)
    if sa.strip():
        return sa
    return _effective_spec_block_for_doc_chain(state, log_node=log_node)


# ---------------------------------------------------------------------------
# Context budget — config-driven only (no hardcoded heuristics)
# ---------------------------------------------------------------------------
#
# The pipeline does NOT encode which step needs which context — that is
# workflow knowledge and belongs in configuration, not code (see §3, §8
# of docs/review-rules.md). Per-step defaults live in JSON
# (``context_budget_profiles.json``); operators override them via
# ``agent_config.swarm.context_budgets`` or ``SWARM_CONTEXT_<FIELD>(_<STEP>)``
# env vars. The Python module
# ``backend/App/orchestration/application/context_budget.py`` owns the
# resolution logic and the typed :class:`ContextBudget` dataclass.
#
# This shim returns a plain dict so existing callers that look up
# ``budget["wiki_chars"]`` etc. keep working without translation. New
# callers should prefer :func:`get_context_budget` (typed dataclass).


def _context_budget(
    step_id: str,
    agent_config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the resolved context budget for *step_id* as a plain dict.

    Thin wrapper around
    :func:`backend.App.orchestration.application.context_budget.get_context_budget`
    that returns ``asdict(budget)`` so legacy callers keep their dict
    look-ups (``budget["wiki_chars"]``, ``budget["include_summaries"]``,
    ``budget.get("knowledge_chars", …)``) unchanged.

    See ``context_budget.py`` for the full resolution order.
    """
    from backend.App.orchestration.application.context_budget import (
        context_budget_as_dict,
        get_context_budget,
    )

    return context_budget_as_dict(get_context_budget(step_id, agent_config))


def _pipeline_context_block(state: PipelineState, current_step_id: str) -> str:
    """Brief pipeline context block: previous/current/next steps."""
    from backend.App.orchestration.application.pipeline_graph import PIPELINE_STEP_REGISTRY

    agent_config = state.get("agent_config") if isinstance(state, dict) else None
    budget = _context_budget(current_step_id, agent_config)
    raw_step_ids: Any = state.get("_pipeline_step_ids")
    step_ids: list[str] = list(raw_step_ids) if isinstance(raw_step_ids, list) else []

    try:
        idx = step_ids.index(current_step_id)
    except ValueError:
        idx = -1

    def _label(step_id: str) -> str:
        row = PIPELINE_STEP_REGISTRY.get(step_id)
        return row[0] if row else step_id

    def _content_steps(ids: list[str]) -> list[str]:
        return [s for s in ids if s in PIPELINE_STEP_REGISTRY
                and not s.startswith(("review_", "human_"))]

    lines: list[str] = ["[Pipeline context]"]

    if step_ids and idx >= 0:
        prev_content = _content_steps(step_ids[:idx])
        next_content = _content_steps(step_ids[idx + 1:])
        if prev_content:
            lines.append("Completed: " + " → ".join(prev_content))
        lines.append(f"Current step: {current_step_id} — {_label(current_step_id)}")
        if next_content:
            lines.append("Next steps: " + " → ".join(next_content[:4]))
    else:
        lines.append(f"Current step: {current_step_id} — {_label(current_step_id)}")

    # Only include agent summaries that are NOT already in the merged spec.
    # When spec_output exists, PM/BA/Arch are already merged there — no need to
    # repeat them as separate 300-char previews (saves ~900 tokens).
    # Role-aware: skip summaries entirely for steps that don't need them (Dev, QA).
    if budget.get("include_summaries", True):
        _has_merged_spec = bool((state.get("spec_output") or "").strip())
        _skip_if_spec = {"pm_output", "ba_output", "arch_output"} if _has_merged_spec else set()
        summaries: list[str] = []
        for key, label in (
            ("clarify_input_human_output", "UserClarification"),
            ("source_research_output", "SourceResearch"),
            ("pm_output", "PM"),
            ("ba_output", "BA"),
            ("arch_output", "Architect"),
            ("spec_output", "Spec"),
            ("devops_output", "DevOps"),
            ("dev_output", "Dev"),
        ):
            if key in _skip_if_spec:
                continue
            val = str(state.get(key) or "").strip()
            if val and key != f"{current_step_id}_output":
                summaries.append(f"  {label}: {val[:300].replace(chr(10), ' ')}…")
        if summaries:
            lines.append("Previous agents summary:")
            lines.extend(summaries)

    # Reload wiki context from disk if workspace_root is available — previous
    # steps may have written new wiki articles via write_step_wiki(). Use
    # semantic retrieval scoped to the current step so the agent gets the
    # *relevant* slice of the wiki, not a flat dump of the first articles.
    workspace_root = (state.get("workspace_root") or "").strip()
    if workspace_root:
        try:
            from backend.App.orchestration.application.wiki_context_loader import (
                load_wiki_context,
                query_for_pipeline_step,
            )
            wiki_query = query_for_pipeline_step(state, current_step_id)
            fresh_wiki = load_wiki_context(workspace_root, query=wiki_query or None)
            if fresh_wiki:
                state["wiki_context"] = fresh_wiki
        except Exception:
            pass  # non-critical — fall back to initial wiki_context

    # Role-aware wiki injection: wiki_chars=0 skips wiki entirely for steps
    # that don't benefit from it (Dev, QA, dev_lead — they have spec + code).
    _wiki_chars = int(budget.get("wiki_chars", 6000) or 0)
    wiki_ctx = (state.get("wiki_context") or "").strip()
    if wiki_ctx and _wiki_chars > 0:
        lines.append("\n[Project wiki memory]")
        # Smart context: rank wiki text by relevance to the step query when
        # SWARM_SMART_CONTEXT=1; otherwise fall back to positional truncation.
        try:
            from backend.App.orchestration.application.smart_context_builder import (
                build_context,
                smart_context_enabled,
            )
            _step_query = wiki_query if workspace_root else ""
            if smart_context_enabled() and _step_query:
                wiki_ctx = build_context(
                    [("Wiki", wiki_ctx)],
                    query=_step_query,
                    budget_chars=_wiki_chars,
                )
            elif len(wiki_ctx) > _wiki_chars:
                wiki_ctx = wiki_ctx[:_wiki_chars] + "\n…[wiki truncated]"
        except Exception:
            if len(wiki_ctx) > _wiki_chars:
                wiki_ctx = wiki_ctx[:_wiki_chars] + "\n…[wiki truncated]"
        # Wrap with untrusted markers — wiki may contain content written by external
        # tools or injected via dependencies; signal to model it is context, not instruction.
        try:
            from backend.App.orchestration.application.untrusted_content import wrap_untrusted
            wiki_ctx = wrap_untrusted(wiki_ctx, source="project_wiki")
        except Exception:
            pass
        lines.append(wiki_ctx)

    return "\n".join(lines) + "\n\n"


def _project_knowledge_block(
    state: PipelineState,
    *,
    max_chars: int = 2500,
    step_id: Optional[str] = None,
) -> str:
    """Compact shared project context: workspace structure + docs.

    Injected into every agent so they all work from the same project model.
    Based on ``workspace_evidence_brief`` collected by PM before any agent runs.
    Returns empty string when no evidence is available.

    When *step_id* is supplied, the per-step context budget from configuration
    (see :func:`_context_budget`) may lower ``max_chars`` or skip the block
    entirely (``knowledge_chars`` = 0).
    """
    brief = str(state.get("workspace_evidence_brief") or "").strip()
    if not brief:
        return ""
    # Config-driven cap: budget may lower max_chars from configuration
    if step_id:
        agent_config = state.get("agent_config") if isinstance(state, dict) else None
        budget = _context_budget(step_id, agent_config)
        knowledge_chars = int(budget.get("knowledge_chars", max_chars) or 0)
        if knowledge_chars <= 0:
            return ""
        max_chars = min(max_chars, knowledge_chars)
    if len(brief) > max_chars:
        brief = brief[:max_chars] + "\n…[workspace brief truncated]"
    # Wrap with untrusted markers — workspace_evidence_brief may contain content
    # from external repositories or user-authored files that could carry injections.
    try:
        from backend.App.orchestration.application.untrusted_content import wrap_untrusted
        brief = wrap_untrusted(brief, source="workspace_evidence")
    except Exception:
        pass
    return (
        "[Project knowledge — workspace structure and documentation]\n"
        + brief
        + "\n\n"
    )


def _dev_sibling_tasks_block(all_tasks: list[dict], current_index: int) -> str:
    """Show all subtasks and their expected file ownership.

    Informs the current dev agent which files belong to sibling subtasks
    so it does not accidentally overwrite or duplicate them.
    """
    if len(all_tasks) <= 1:
        return ""
    lines: list[str] = ["[File ownership across all subtasks — avoid conflicts]"]
    for j, t in enumerate(all_tasks):
        tid = str(t.get("id") or j + 1)
        title = str(t.get("title") or f"T{j + 1}")[:60]
        paths = [str(p) for p in (t.get("expected_paths") or []) if str(p).strip()]
        marker = " ← THIS SUBTASK" if j == current_index else ""
        if paths:
            lines.append(f"  [{tid}] {title}{marker}: {', '.join(paths[:6])}")
        else:
            lines.append(f"  [{tid}] {title}{marker}: (no declared paths)")
    lines.append(
        "RULE: do NOT write to files listed under other subtasks above unless your scope explicitly requires it."
    )
    return "\n".join(lines) + "\n\n"
