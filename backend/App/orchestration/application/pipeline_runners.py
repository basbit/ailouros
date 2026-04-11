"""Streaming pipeline runner functions.

Extracted from pipeline_graph.py to keep that module under 500 lines.
These functions iterate over pipeline steps and yield SSE-ready progress events.

All entry points:
- run_pipeline_stream       — initial run with progress events
- run_pipeline_stream_resume — resume after awaiting_human gate
- run_pipeline_stream_retry  — retry from a failed step
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from collections.abc import Generator
from typing import Any, Optional, cast

from backend.App.orchestration.domain.exceptions import HumanApprovalRequired, PipelineCancelled
from backend.App.orchestration.application.pipeline_enforcement import (
    _CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY,
    _PLANNING_REVIEW_RESUME_STEP,
    enter_fix_cycle_or_escalate as _enter_fix_cycle_or_escalate,
    finalize_pipeline_machine as _finalize_pipeline_machine,
    enforce_planning_review_gate as _enforce_planning_review_gate,
    prepare_pipeline_machine_for_step as _prepare_pipeline_machine_for_step,
    require_structured_blockers as _require_structured_blockers,
    run_post_dev_verification_gates as _run_post_dev_verification_gates,
    run_post_step_enforcement as _run_post_step_enforcement,
    sync_pipeline_machine as _sync_pipeline_machine,
    transition_pipeline_phase as _transition_pipeline_phase,
)
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.application.pipeline_runtime_support import (
    finalize_pipeline_metrics as _finalize_pipeline_metrics,
    record_open_defects as _record_open_defects,
)
from backend.App.orchestration.domain.pipeline_machine import (
    PipelineMachine,
    PipelinePhase,
    get_pipeline_machine,
    reset_pipeline_machine,
)

_logger = logging.getLogger(__name__)

__all__ = (
    "run_pipeline_stream",
    "run_pipeline_stream_resume",
    "run_pipeline_stream_retry",
    "run_pipeline_stream_staged",
    "_PLANNING_REVIEW_RESUME_STEP",
    "_enforce_planning_review_gate",
    "_require_structured_blockers",
    "_run_post_dev_verification_gates",
    "_transition_pipeline_phase",
    "_enter_fix_cycle_or_escalate",
    "_record_open_defects",
)

# Fix B: track NEEDS_WORK verdicts from critical review steps
_NEEDS_WORK_WARNING_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Task-class router: detect research/plan vs implementation tasks
# Default: ON (SWARM_TASK_CLASS_ROUTER=1). Disable with SWARM_TASK_CLASS_ROUTER=0.
# ---------------------------------------------------------------------------

_IMPLEMENTATION_KEYWORDS: frozenset[str] = frozenset({
    # Russian
    "реализуй", "реализовать", "напиши", "написать", "создай", "создать",
    "добавь", "добавить", "измени", "изменить", "исправь", "исправить",
    "сделай", "сделать", "внедри", "внедрить", "поправь", "поправить",
    "удали", "удалить", "переименуй", "рефакторинг", "рефактор",
    "задеплой", "деплой", "настрой", "настроить",
    # English
    "implement", "create", "write", "build", "fix", "add", "modify",
    "update", "delete", "remove", "refactor", "develop", "deploy",
    "configure", "setup", "integrate", "install", "migrate",
})

_RESEARCH_PLAN_KEYWORDS: frozenset[str] = frozenset({
    # Russian
    "найди", "найти", "поищи", "поиск", "изучи", "изучить", "оцени",
    "расскажи", "объясни", "объяснить", "проанализируй", "проанализировать",
    "исследуй", "исследовать", "составь план", "документацию", "план",
    "список", "обзор", "что такое", "как работает", "покажи",
    # English
    "find", "research", "plan", "analyze", "explain", "study",
    "list", "survey", "document", "overview", "summarize", "compare",
    "what is", "how does", "show me", "describe",
})

# Steps used for research/plan tasks (no dev/qa phases)
_RESEARCH_PLAN_STEP_IDS: list[str] = [
    "clarify_input",
    "human_clarify_input",
    "analyze_code",
    "pm",
    "review_pm",
    "human_pm",
]


def _detect_task_class(user_input: str) -> str:
    """Classify user task as 'research_plan' or 'implementation'.

    Returns 'research_plan' only when task has clear research/plan signals
    AND no implementation signals. Defaults to 'implementation' when ambiguous.

    Enable router via SWARM_TASK_CLASS_ROUTER=1.
    """
    lowered = (user_input or "").lower()
    has_impl = any(kw in lowered for kw in _IMPLEMENTATION_KEYWORDS)
    if has_impl:
        return "implementation"
    has_research = any(kw in lowered for kw in _RESEARCH_PLAN_KEYWORDS)
    if has_research:
        return "research_plan"
    return "implementation"


def _auto_select_pipeline_steps(
    user_input: str,
    agent_config: dict[str, Any],
    default_steps: list[str],
) -> list[str]:
    """Return pipeline step list auto-selected by task class.

    Only active when SWARM_TASK_CLASS_ROUTER=1. Otherwise returns *default_steps*.
    Can be overridden per-task via agent_config.swarm.task_class.
    """
    if os.getenv("SWARM_TASK_CLASS_ROUTER", "1").strip().lower() not in ("1", "true", "yes", "on"):
        return default_steps
    # Allow explicit override from agent_config
    swarm_cfg = (agent_config or {}).get("swarm") or {}
    explicit_class = str(swarm_cfg.get("task_class") or "").strip().lower()
    task_class = explicit_class if explicit_class in ("research_plan", "implementation") else _detect_task_class(user_input)
    if task_class == "research_plan":
        _logger.info(
            "task_class_router: detected 'research_plan' — using reduced step set %s",
            _RESEARCH_PLAN_STEP_IDS,
        )
        return _RESEARCH_PLAN_STEP_IDS
    return default_steps


# ---------------------------------------------------------------------------
# R1.4 — Trace helper (avoids repeated imports inside the hot step loop)
# ---------------------------------------------------------------------------

def _tc_emit_event(tc: Any, task_id: str, session_id: str, step: str, event_type_val: str, data: dict) -> str | None:
    """Emit a trace event; return event_id on success, None on failure.

    All imports are lazy to prevent import-time failures from blocking the pipeline.
    """
    try:
        from datetime import datetime, timezone
        import uuid
        from backend.App.orchestration.domain.trace import TraceEvent, EventType
        event_id = str(uuid.uuid4())
        tc.record(TraceEvent(
            event_id=event_id,
            trace_id=task_id,
            session_id=session_id,
            task_id=task_id,
            step=step,
            event_type=EventType(event_type_val),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            data=data,
        ))
        return event_id
    except Exception as exc:
        _logger.debug("Trace emit_event(%s) skipped: %s", event_type_val, exc)
        return None


def _tc_emit_child_event(
    tc: Any, task_id: str, session_id: str, step: str, event_type_val: str,
    parent_event_id: str | None, data: dict,
) -> str | None:
    """Emit a trace event with optional parent; return event_id on success."""
    try:
        from datetime import datetime, timezone
        import uuid
        from backend.App.orchestration.domain.trace import TraceEvent, EventType
        event_id = str(uuid.uuid4())
        tc.record(TraceEvent(
            event_id=event_id,
            trace_id=task_id,
            session_id=session_id,
            task_id=task_id,
            step=step,
            event_type=EventType(event_type_val),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            data=data,
            parent_event_id=parent_event_id,
        ))
        return event_id
    except Exception as exc:
        _logger.debug("Trace emit_child_event(%s) skipped: %s", event_type_val, exc)
        return None


def _run_pipeline_stream_graph(
    user_input: str,
    agent_config: dict[str, Any],
    workspace_root: str,
    workspace_apply_writes: bool,
    task_id: str,
    cancel_event: Optional[threading.Event],
    topology: str,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    """Run pipeline via LangGraph compiled graph with topology support.

    Delegates to ``PipelineGraphBuilder.build_for_topology`` and streams events
    from the graph execution, yielding SSE-compatible progress dicts.
    """
    from backend.App.orchestration.application.graph_builder import PipelineGraphBuilder
    from backend.App.orchestration.application.pipeline_state_helpers import (
        _initial_pipeline_state,
    )

    _logger.info("Using LangGraph graph for topology=%r (stream mode)", topology)
    compiled = PipelineGraphBuilder().build_for_topology(topology, agent_config)
    init = _initial_pipeline_state(
        user_input,
        agent_config,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=pipeline_step_ids,
    )

    final_state: PipelineState = cast(PipelineState, dict(init))
    prev_keys: set[str] = set(init.keys())

    # Map LangGraph node names to pipeline step IDs for SSE events
    _NODE_TO_STEP: dict[str, str] = {
        "PM": "pm", "REVIEW_PM": "review_pm", "HUMAN_PM": "human_pm",
        "BA": "ba", "REVIEW_BA": "review_ba", "HUMAN_BA": "human_ba",
        "ARCH": "architect", "REVIEW_STACK": "review_stack",
        "REVIEW_ARCH": "review_arch", "HUMAN_ARCH": "human_arch",
        "SPEC_MERGE": "spec_merge", "REVIEW_SPEC": "review_spec",
        "HUMAN_SPEC": "human_spec",
        "ANALYZE_CODE": "analyze_code",
        "GENERATE_DOCUMENTATION": "generate_documentation",
        "PROBLEM_SPOTTER": "problem_spotter", "REFACTOR_PLAN": "refactor_plan",
        "HUMAN_CODE_REVIEW": "human_code_review",
        "DEVOPS": "devops", "REVIEW_DEVOPS": "review_devops",
        "HUMAN_DEVOPS": "human_devops",
        "DEV_LEAD": "dev_lead", "REVIEW_DEV_LEAD": "review_dev_lead",
        "HUMAN_DEV_LEAD": "human_dev_lead",
        "DEV": "dev", "VERIFICATION_LAYER": "verification_layer", "REVIEW_DEV": "review_dev",
        "DEV_RETRY_GATE": "dev_retry_gate", "HUMAN_DEV": "human_dev",
        "QA": "qa", "REVIEW_QA": "review_qa", "QA_RETRY_GATE": "qa_retry_gate",
        "HUMAN_QA": "human_qa", "FINALIZE_PIPELINE": "finalize_pipeline",
    }
    _seen_nodes: set[str] = set()

    try:
        for event in compiled.stream(init, config={"recursion_limit": 96}):
            if cancel_event is not None and cancel_event.is_set():
                raise PipelineCancelled("pipeline cancelled (client disconnect or server shutdown)")
            # LangGraph stream yields {node_name: {updated_state_keys...}}
            for node_name, updates in event.items():
                if not isinstance(updates, dict):
                    continue
                agent_name = _NODE_TO_STEP.get(node_name, node_name.lower())

                # Emit in_progress before first completion of this node
                if node_name not in _seen_nodes:
                    _seen_nodes.add(node_name)
                    yield {"agent": agent_name, "status": "in_progress", "message": f"{agent_name} started"}

                cast(dict, final_state).update(updates)
                # Detect which output key changed → emit completed event
                output_key = next(
                    (k for k in updates if k.endswith("_output") and isinstance(updates[k], str)),
                    None,
                )
                if output_key:
                    yield {
                        "agent": agent_name,
                        "status": "completed",
                        "message": str(updates[output_key])[:500],
                        "model": updates.get(output_key.replace("_output", "_model"), ""),
                        "provider": updates.get(output_key.replace("_output", "_provider"), ""),
                    }
                else:
                    yield {"agent": agent_name, "status": "completed", "message": ""}
                prev_keys.update(updates.keys())
    except HumanApprovalRequired:
        raise
    except PipelineCancelled:
        raise
    except Exception as exc:
        setattr(exc, "_partial_state", copy.deepcopy(final_state))
        setattr(exc, "_failed_step", "graph")
        raise

    _finalize_pipeline_metrics(final_state)
    return final_state


def run_pipeline_stream(
    user_input: str,
    agent_config: Optional[dict[str, Any]] = None,
    pipeline_steps: Optional[list[str]] = None,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    cancel_event: Optional[threading.Event] = None,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    """Run pipeline step-by-step and yield agent progress events.

    Порядок шагов линеен (сначала BA-ветка, затем ARCH): при ``pipeline_steps=None``
    совпадает с прежним списком. Кастомный список — тот же реестр узлов, по порядку.
    """
    from backend.App.orchestration.application.pipeline_graph import (
        DEFAULT_PIPELINE_STEP_IDS,
        _compact_state_if_needed,
        _emit_completed,
        _initial_pipeline_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _run_step_with_stream_progress,
        _state_snapshot,
        validate_pipeline_steps,
    )
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    base_agent_config = agent_config or {}
    _default_steps = DEFAULT_PIPELINE_STEP_IDS
    if pipeline_steps is None:
        _default_steps = _auto_select_pipeline_steps(user_input, base_agent_config, DEFAULT_PIPELINE_STEP_IDS)
    steps_ids = pipeline_steps if pipeline_steps is not None else _default_steps
    validate_pipeline_steps(steps_ids, base_agent_config)
    reset_pipeline_machine()
    machine = get_pipeline_machine()

    # P0-10: Register pipeline task in ContractValidator for execution limits tracking
    if task_id:
        from backend.App.orchestration.domain.contract_validator import ContractViolation
        from backend.App.orchestration.infrastructure.runtime_policy import get_runtime_validator
        try:
            _cv = get_runtime_validator()
            _cv.register_task(task_id, "orchestrator")
            _logger.debug("ContractValidator: registered task %s", task_id)
        except ContractViolation:
            pass  # task already registered (e.g. retry)

    # Topology: use LangGraph graph ONLY when user did not specify pipeline_steps.
    # When user defined steps, always use linear runner (respects user's order).
    _topo = (base_agent_config.get("swarm") or {}).get("topology", "") if isinstance(base_agent_config, dict) else ""
    if _topo and _topo not in ("", "linear", "default") and pipeline_steps is None:
        yield from _run_pipeline_stream_graph(
            user_input, base_agent_config, workspace_root, workspace_apply_writes,
            task_id, cancel_event, _topo,
            pipeline_workspace_parts=pipeline_workspace_parts,
            pipeline_step_ids=steps_ids,
        )
        return
    step_ids_for_warn = (
        pipeline_step_ids if pipeline_step_ids is not None else steps_ids
    )
    state = _initial_pipeline_state(
        user_input,
        base_agent_config,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=step_ids_for_warn,
    )
    cast(dict, state)["_pipeline_step_ids"] = list(steps_ids)
    _sync_pipeline_machine(state, machine)

    # R1.1 — create durable session; R1.4 — get trace collector
    _session_id: str | None = None
    _sm = None   # SessionManager | None
    _tc = None   # TraceCollectorPort | None
    try:
        from backend.App.orchestration.infrastructure._singletons import (
            get_session_manager,
            get_trace_collector,
        )
        _sm = get_session_manager()
        _tc = get_trace_collector()
        _sess = _sm.create_session(task_id, metadata={"steps": list(steps_ids)})
        _session_id = _sess.session_id
        # Expose session_id in pipeline state for downstream use
        cast(dict, state)["_session_id"] = _session_id
        # Emit RUN_START trace event
        _tc_emit_event(_tc, task_id, _session_id, "pipeline", "run_start", {"steps": list(steps_ids)})
    except Exception as _sinit_exc:
        _logger.debug("Session/trace init skipped: %s", _sinit_exc)
        # _sm and _tc remain None — all R1.1/R1.4 guards skip gracefully

    # Wrap loop in try-finally so session/trace cleanup happens even on unexpected exceptions
    try:
        for step_id in steps_ids:
            if _pipeline_should_cancel(state):
                raise PipelineCancelled(
                    "pipeline cancelled (client disconnect or server shutdown)"
                )
            _prepare_pipeline_machine_for_step(state, machine, step_id)
            # C-1: compact state before each step if it exceeds SWARM_STATE_MAX_CHARS
            compaction_event = _compact_state_if_needed(state, step_id)
            if compaction_event is not None:
                yield compaction_event
            progress_message, step_func = _resolve_pipeline_step(step_id, base_agent_config)
            progress_message = pipeline_step_in_progress_message(step_id, state)
            yield {"agent": step_id, "status": "in_progress", "message": progress_message}
            # P0-10/§10.3-5: track per-step state
            if task_id:
                from backend.App.orchestration.domain.contract_validator import get_validator as _get_cv
                _get_cv().step_start(task_id, step_id)
            # R1.4 — emit STEP_START trace event
            _step_event_id: str | None = None
            if _tc is not None and _session_id:
                _step_event_id = _tc_emit_event(_tc, task_id, _session_id, step_id, "step_start", {})
            try:
                # Всегда в worker + heartbeat: иначе один next() синхронного SSE-генератора
                # блокирует весь шаг (PM/BA/…) без промежуточных yield — клиент и ASGI «молчат».
                yield from _run_step_with_stream_progress(step_id, step_func, state)
            except HumanApprovalRequired as exc:
                exc.partial_state = _state_snapshot(state)
                # Preserve resume_pipeline_step if the node already set it
                # (e.g. clarify_input → human_clarify_input).
                if not exc.resume_pipeline_step:
                    exc.resume_pipeline_step = step_id
                # R1.1 — pause session on human approval
                if _sm is not None and _session_id:
                    try:
                        from backend.App.orchestration.domain.session import SessionStatus
                        _sm._update_status(_session_id, SessionStatus.PAUSED)
                    except Exception:
                        pass
                raise
            except PipelineCancelled:
                raise
            except Exception as exc:
                # Attach state snapshot so orchestrator can offer "retry from this step"
                setattr(exc, "_partial_state", _state_snapshot(state))
                setattr(exc, "_failed_step", step_id)
                if task_id:
                    from backend.App.orchestration.domain.contract_validator import get_validator as _get_cv
                    _get_cv().step_error(task_id, step_id, str(exc)[:200])
                # R1.4 — emit ERROR trace event
                if _tc is not None and _session_id:
                    _tc_emit_child_event(
                        _tc, task_id, _session_id, step_id, "error",
                        _step_event_id, {"error": str(exc)[:500]},
                    )
                # R1.1 — fail the session
                if _sm is not None and _session_id:
                    try:
                        _sm.fail_session(_session_id, reason=str(exc)[:500])
                    except Exception:
                        pass
                raise
            yield _emit_completed(step_id, state)
            # P0-10: mark step DONE
            if task_id:
                from backend.App.orchestration.domain.contract_validator import get_validator as _get_cv
                _get_cv().step_complete(task_id, step_id)
            # R1.4 — emit STEP_END trace event
            if _tc is not None and _session_id:
                _tc_emit_child_event(_tc, task_id, _session_id, step_id, "step_end", _step_event_id, {})
            # R1.1 — checkpoint session after each step
            if _sm is not None and _session_id:
                try:
                    _sm.checkpoint(_session_id, step_id, {"step": step_id, "task_id": task_id})
                except Exception as _ce:
                    _logger.debug("Session checkpoint failed after %s: %s", step_id, _ce)

            yield from _run_post_step_enforcement(
                state,
                machine,
                step_id,
                base_agent_config,
                _resolve_pipeline_step,
                _run_step_with_stream_progress,
                _emit_completed,
            )

            # P0-1b: Gate after analyze_code — block pipeline on empty or too-large scope
            if step_id == "analyze_code":
                _ac_out = str(state.get("analyze_code_output") or "").strip()
                _ac_data = state.get("code_analysis") if isinstance(state.get("code_analysis"), dict) else {}
                _ac_file_count = int(_ac_data.get("file_count", 0)) if _ac_data else 0
                _ac_max_files = int(os.environ.get("SWARM_ANALYZE_CODE_MAX_FILES", "300"))

                if not _ac_out or len(_ac_out) < 20:
                    _logger.error(
                        "P0-1b: analyze_code returned empty/near-empty output — "
                        "pipeline paused (NEEDS_CLARIFICATION). workspace_root=%s",
                        state.get("workspace_root", ""),
                    )
                    raise HumanApprovalRequired(
                        step="analyze_code",
                        detail=(
                            "analyze_code returned empty or near-empty output. "
                            "The workspace may be misconfigured or empty. "
                            "Please check workspace_root and ensure the project has source files, "
                            "then retry."
                        ),
                        resume_pipeline_step="human_code_review",
                        partial_state={"analyze_code_output": _ac_out},
                    )
                elif _ac_file_count > _ac_max_files:
                    _logger.warning(
                        "P0-1b: analyze_code found %d files (limit %d) — "
                        "pipeline paused (NEEDS_CLARIFICATION). workspace_root=%s",
                        _ac_file_count, _ac_max_files, state.get("workspace_root", ""),
                    )
                    raise HumanApprovalRequired(
                        step="analyze_code",
                        detail=(
                            f"analyze_code found {_ac_file_count} files (limit: {_ac_max_files}). "
                            "The project scope is too large for a single pipeline run. "
                            "Please narrow the scope: specify a subdirectory or reduce the file set, "
                            "or increase SWARM_ANALYZE_CODE_MAX_FILES if this is intentional."
                        ),
                        resume_pipeline_step="human_code_review",
                        partial_state={"analyze_code_output": _ac_out},
                    )

            # Quality check: warn when LLM-producing steps return weak output
            _STEP_MIN_OUTPUT: dict[str, tuple[str, int]] = {
                "pm": ("pm_output", 300),
                "ba": ("ba_output", 200),
                "architect": ("arch_output", 500),
                "devops": ("devops_output", 200),
                "dev_lead": ("dev_lead_output", 200),
                "dev": ("dev_output", 300),
                "qa": ("qa_output", 200),
                "spec_merge": ("spec_output", 200),
                "generate_documentation": ("generate_documentation_output", 200),
            }
            if step_id in _STEP_MIN_OUTPUT:
                _qc_key, _qc_min = _STEP_MIN_OUTPUT[step_id]
                _qc_val = str(state.get(_qc_key) or "").strip()
                if len(_qc_val) < _qc_min:
                    yield {
                        "agent": "orchestrator",
                        "status": "warning",
                        "message": (
                            f"⚠ [{step_id}] output too short ({len(_qc_val)} chars, min {_qc_min}). "
                            "The model may lack context or capability for this role. "
                            "Consider: 1) a more capable model, 2) remote_profile for this role, "
                            "3) check that workspace_root is correct."
                        ),
                    }

            # CTX-2: cleanup intermediate outputs after they are no longer needed
            _STEP_CLEANUP: dict[str, list[str]] = {
                "spec_merge": [
                    "review_pm_output", "review_pm_model", "review_pm_provider",
                    "review_ba_output", "review_ba_model", "review_ba_provider",
                    "review_arch_output", "review_arch_model", "review_arch_provider",
                    "stack_review_output", "stack_review_model", "stack_review_provider",
                    "ba_arch_debate_output", "ba_arch_debate_model", "ba_arch_debate_provider",
                ],
                "human_code_review": [
                    "review_spec_output", "review_spec_model", "review_spec_provider",
                    "generate_documentation_output", "generate_documentation_model",
                    "problem_spotter_output", "problem_spotter_model",
                    "refactor_plan_output", "refactor_plan_model",
                ],
            }
            _to_clean = _STEP_CLEANUP.get(step_id, [])
            if _to_clean:
                for _ck in _to_clean:
                    if _ck in state and isinstance(state.get(_ck), str):
                        cast(dict, state)[_ck] = ""
                _logger.debug("CTX-2 cleanup after %s: cleared %d keys", step_id, len(_to_clean))

            # P3: log state size after each step
            try:
                import json as _jmod
                _state_size = len(_jmod.dumps(
                    {k: v for k, v in state.items() if not str(k).startswith("_")},
                    ensure_ascii=False, default=str,
                ))
                if _state_size > 500_000:
                    _logger.error(
                        "State size CRITICAL after %s: %d chars (~%dK tokens). "
                        "Context overflow likely on next step.",
                        step_id, _state_size, _state_size // 4000,
                    )
                elif _state_size > 200_000:
                    _logger.warning(
                        "State size WARNING after %s: %d chars (~%dK tokens)",
                        step_id, _state_size, _state_size // 4000,
                    )
            except Exception as exc:
                _logger.debug("State size measurement failed after %s: %s", step_id, exc)

            # B1: warn if pm/architect returned intent-only output
            _PLANNING_STEPS_OUTPUT_KEYS = {
                "pm": "pm_output",
                "architect": "arch_output",
                "ba": "ba_output",
            }
            if step_id in _PLANNING_STEPS_OUTPUT_KEYS:
                _out_key = _PLANNING_STEPS_OUTPUT_KEYS[step_id]
                _out_val = str(state.get(_out_key) or "")
                _first_line = _out_val.strip().lower().split("\n")[0]
                _INTENT_PREFIXES = (
                    "i'll analyze", "let me first", "i'll create", "i will analyze",
                    "let me analyze", "i'll examine", "i'll start by", "i'll check",
                    "let me check",
                )
                if len(_out_val.strip()) < 300 or any(_first_line.startswith(p) for p in _INTENT_PREFIXES):
                    yield {
                        "agent": "orchestrator",
                        "status": "warning",
                        "message": (
                            f"⚠ [{step_id}] вернул пустой или intent-only вывод ({len(_out_val.strip())} chars). "
                            "Возможно, модель пытается вызвать MCP tools без поддержки. "
                            "Попробуйте: 1) включить SWARM_MCP_TOOL_CALL_FALLBACK=1, "
                            "2) выбрать другую модель для этой роли."
                        ),
                    }
                    # Step 1.4: signal inline fallback for next planning step
                    state["mcp_tool_call_suspected_failure"] = True

            # Fix B: warn when multiple critical reviewers return NEEDS_WORK
            if step_id in _CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY:
                from backend.App.orchestration.application.pipeline_graph import (
                    _extract_verdict,
                )
                review_text = state.get(_CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY[step_id]) or ""
                if _extract_verdict(review_text) == "NEEDS_WORK":
                    nw = int(state.get("_needs_work_count") or 0) + 1
                    state["_needs_work_count"] = nw
                    if nw >= _NEEDS_WORK_WARNING_THRESHOLD:
                        yield {
                            "agent": "orchestrator",
                            "status": "warning",
                            "message": (
                                f"⚠ {nw} critical reviewers returned NEEDS_WORK "
                                f"(latest: {step_id}) — upstream agents may have produced "
                                "empty or trivial output. "
                                "Check model capabilities and whether clarify_input questions were answered."
                            ),
                        }

            if state.get("_pipeline_stop_early"):
                reason = state.get("_pipeline_stop_reason") or ""
                if reason:
                    yield {"agent": step_id, "status": "warning", "message": reason}
                break

            # P0-10: Mark task as DONE in ContractValidator (inside try so finally still runs)
            if task_id:
                from backend.App.orchestration.domain.contract_validator import ContractViolation
                from backend.App.orchestration.infrastructure.runtime_policy import get_runtime_validator
                try:
                    get_runtime_validator().transition_task(task_id, "DONE")
                except ContractViolation as exc:
                    _logger.warning("ContractValidator: could not mark task %s as DONE: %s", task_id, exc)

            # R1.1 — complete session on normal exit
            if _sm is not None and _session_id:
                try:
                    _sm.complete_session(_session_id)
                except Exception as _ce:
                    _logger.debug("Session complete failed: %s", _ce)

    finally:
        # R1.4 — always emit RUN_END trace so the session is closed even on exception
        if _tc is not None and _session_id:
            _tc_emit_event(_tc, task_id, _session_id, "pipeline", "run_end", {})

    _finalize_pipeline_machine(state, machine)

    return state


def run_pipeline_stream_resume(
    partial_state: PipelineState,
    pipeline_steps: list[str],
    resume_from_step: str,
    human_feedback_text: str,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    """Продолжить линейный пайплайн после ручного human-шага (см. POST human-resume)."""
    from backend.App.orchestration.application.pipeline_graph import (
        HUMAN_PIPELINE_STEP_TO_STATE_KEY,
        _emit_completed,
        _migrate_legacy_pm_tasks_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _run_step_with_stream_progress,
        _state_snapshot,
        format_human_resume_output,
        validate_pipeline_steps,
    )
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    resume_agent_config = (
        partial_state.get("agent_config")
        if isinstance(partial_state.get("agent_config"), dict)
        else {}
    )
    validate_pipeline_steps(pipeline_steps, resume_agent_config)
    out_key = HUMAN_PIPELINE_STEP_TO_STATE_KEY.get(resume_from_step)
    if not out_key:
        raise ValueError(f"Неизвестный human-шаг: {resume_from_step!r}")
    try:
        idx = pipeline_steps.index(resume_from_step)
    except ValueError as exc:
        # clarify_input auto-pause: human_clarify_input may not be in the configured
        # step list (user may not have added it). Inject it after clarify_input.
        if resume_from_step == "human_clarify_input":
            pipeline_steps = list(pipeline_steps)
            try:
                insert_idx = pipeline_steps.index("clarify_input") + 1
            except ValueError:
                insert_idx = 0
            pipeline_steps.insert(insert_idx, resume_from_step)
            idx = pipeline_steps.index(resume_from_step)
        else:
            raise ValueError(
                f"Шаг {resume_from_step!r} не найден в pipeline_steps"
            ) from exc

    state: dict[str, Any] = copy.deepcopy(partial_state)
    _migrate_legacy_pm_tasks_state(state)
    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    if cancel_event is not None:
        state["_pipeline_cancel_event"] = cancel_event
    state["_pipeline_step_ids"] = list(pipeline_steps)
    state[out_key] = format_human_resume_output(
        resume_from_step, human_feedback_text
    )
    _sync_pipeline_machine(state, machine)

    for step_id in pipeline_steps[idx + 1:]:
        if _pipeline_should_cancel(state):
            raise PipelineCancelled(
                "pipeline cancelled (client disconnect or server shutdown)"
            )
        _prepare_pipeline_machine_for_step(state, machine, step_id)
        progress_message, step_func = _resolve_pipeline_step(step_id, resume_agent_config)
        progress_message = pipeline_step_in_progress_message(step_id, state)
        yield {"agent": step_id, "status": "in_progress", "message": progress_message}
        try:
            yield from _run_step_with_stream_progress(step_id, step_func, state)
        except HumanApprovalRequired as exc:
            exc.partial_state = _state_snapshot(state)
            if not exc.resume_pipeline_step:
                exc.resume_pipeline_step = step_id
            raise
        except PipelineCancelled:
            raise
        except Exception as exc:
            setattr(exc, "_partial_state", _state_snapshot(state))
            setattr(exc, "_failed_step", step_id)
            raise
        yield _emit_completed(step_id, state)
        yield from _run_post_step_enforcement(
            state,
            machine,
            step_id,
            resume_agent_config,
            _resolve_pipeline_step,
            _run_step_with_stream_progress,
            _emit_completed,
        )

    _finalize_pipeline_machine(state, machine)
    return state


def run_pipeline_stream_retry(
    partial_state: PipelineState,
    pipeline_steps: list[str],
    from_step: str,
    override_agent_config: Optional[dict[str, Any]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    """Re-run the pipeline starting from a failed step (inclusive).

    Designed for the "retry from failed step" flow: the orchestrator catches a step
    failure, saves partial_state + failed_step to pipeline.json, and the user can
    POST /v1/tasks/{id}/retry with an optional override_agent_config (e.g. to
    switch to a different model) to re-run from that step without losing prior work.
    """
    from backend.App.orchestration.application.pipeline_graph import (
        _emit_completed,
        _migrate_legacy_pm_tasks_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _run_step_with_stream_progress,
        _state_snapshot,
        validate_pipeline_steps,
    )
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    state: dict[str, Any] = copy.deepcopy(partial_state)
    _migrate_legacy_pm_tasks_state(state)
    machine = PipelineMachine.from_dict(state.get("pipeline_machine") or {})
    if cancel_event is not None:
        state["_pipeline_cancel_event"] = cancel_event

    # Merge override_agent_config on top of whatever was stored in partial_state
    if isinstance(override_agent_config, dict) and override_agent_config:
        base_ac: dict[str, Any] = dict(state.get("agent_config") or {})
        # Deep-merge: top-level keys from override win; nested role dicts are merged
        for k, v in override_agent_config.items():
            if isinstance(v, dict) and isinstance(base_ac.get(k), dict):
                base_ac[k] = {**base_ac[k], **v}
            else:
                base_ac[k] = v
        state["agent_config"] = base_ac

    retry_agent_config = state.get("agent_config") or {}
    validate_pipeline_steps(pipeline_steps, retry_agent_config)

    try:
        idx = pipeline_steps.index(from_step)
    except ValueError as exc:
        raise ValueError(
            f"Step {from_step!r} not found in pipeline_steps"
        ) from exc

    state["_pipeline_step_ids"] = list(pipeline_steps)
    _sync_pipeline_machine(state, machine)

    for step_id in pipeline_steps[idx:]:
        if _pipeline_should_cancel(state):
            raise PipelineCancelled(
                "pipeline cancelled (client disconnect or server shutdown)"
            )
        _prepare_pipeline_machine_for_step(state, machine, step_id)
        progress_message, step_func = _resolve_pipeline_step(step_id, retry_agent_config)
        progress_message = pipeline_step_in_progress_message(step_id, state)
        yield {"agent": step_id, "status": "in_progress", "message": progress_message}
        try:
            yield from _run_step_with_stream_progress(step_id, step_func, state)
        except HumanApprovalRequired as exc:
            exc.partial_state = _state_snapshot(state)
            if not exc.resume_pipeline_step:
                exc.resume_pipeline_step = step_id
            raise
        except PipelineCancelled:
            raise
        except Exception as exc:
            setattr(exc, "_partial_state", _state_snapshot(state))
            setattr(exc, "_failed_step", step_id)
            raise
        yield _emit_completed(step_id, state)
        yield from _run_post_step_enforcement(
            state,
            machine,
            step_id,
            retry_agent_config,
            _resolve_pipeline_step,
            _run_step_with_stream_progress,
            _emit_completed,
        )

    _finalize_pipeline_machine(state, machine)
    return state


def run_pipeline_stream_staged(
    user_input: str,
    pipeline_stages: list[list[str]],
    agent_config: Optional[dict[str, Any]] = None,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    cancel_event: Optional[threading.Event] = None,
    *,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
) -> Generator[dict[str, Any], None, PipelineState]:
    """Run pipeline with explicit stages: stages sequential, steps within stage parallel.

    Args:
        pipeline_stages: e.g. [["clarify_input"], ["pm"], ["ba", "architect"], ["dev"], ["qa"]]
    """
    import concurrent.futures
    from backend.App.orchestration.application.pipeline_graph import (
        _compact_state_if_needed,
        _emit_completed,
        _initial_pipeline_state,
        _pipeline_should_cancel,
        _resolve_pipeline_step,
        _run_step_with_stream_progress,
        _state_snapshot,
    )
    from backend.App.orchestration.application.pipeline_display import (
        pipeline_step_in_progress_message,
    )

    base_agent_config = agent_config or {}
    all_step_ids = [step_id for stage in pipeline_stages for step_id in stage]
    machine = PipelineMachine()

    state = _initial_pipeline_state(
        user_input,
        base_agent_config,
        workspace_root=workspace_root,
        workspace_apply_writes=workspace_apply_writes,
        task_id=task_id,
        cancel_event=cancel_event,
        pipeline_workspace_parts=pipeline_workspace_parts,
        pipeline_step_ids=all_step_ids,
    )
    cast(dict, state)["_pipeline_step_ids"] = list(all_step_ids)
    _sync_pipeline_machine(state, machine)

    for stage_idx, stage in enumerate(pipeline_stages):
        if _pipeline_should_cancel(state):
            raise PipelineCancelled(
                "pipeline cancelled (client disconnect or server shutdown)"
            )

        if len(stage) == 1:
            step_id = stage[0]
            _prepare_pipeline_machine_for_step(state, machine, step_id)
            compaction_event = _compact_state_if_needed(state, step_id)
            if compaction_event is not None:
                yield compaction_event
            progress_message, step_func = _resolve_pipeline_step(step_id, base_agent_config)
            progress_message = pipeline_step_in_progress_message(step_id, state)
            yield {"agent": step_id, "status": "in_progress", "message": progress_message}
            try:
                yield from _run_step_with_stream_progress(step_id, step_func, state)
            except HumanApprovalRequired as exc:
                exc.partial_state = _state_snapshot(state)
                if not exc.resume_pipeline_step:
                    exc.resume_pipeline_step = step_id
                raise
            except PipelineCancelled:
                raise
            except Exception as exc:
                setattr(exc, "_partial_state", _state_snapshot(state))
                setattr(exc, "_failed_step", step_id)
                raise
            yield _emit_completed(step_id, state)
            yield from _run_post_step_enforcement(
                state,
                machine,
                step_id,
                base_agent_config,
                _resolve_pipeline_step,
                _run_step_with_stream_progress,
                _emit_completed,
            )
        else:
            active_steps = list(stage)
            disallowed_parallel = [
                sid for sid in active_steps
                if machine.step_phase(sid) != PipelinePhase.PLAN
            ]
            if disallowed_parallel:
                raise ValueError(
                    "Parallel staged execution is only supported for PLAN-phase steps; "
                    f"got non-PLAN steps in one stage: {disallowed_parallel}"
                )
            yield {
                "type": "active_steps",
                "activeSteps": active_steps,
                "stage": stage_idx,
                "status": "in_progress",
                "message": f"Running parallel stage: {', '.join(active_steps)}",
            }
            for step_id in active_steps:
                yield {"agent": step_id, "status": "in_progress",
                       "message": pipeline_step_in_progress_message(step_id, state)}

            step_results: dict[str, dict[str, Any]] = {}
            step_errors: dict[str, Exception] = {}

            def _run_parallel_step(sid: str) -> dict[str, Any]:
                _, step_func = _resolve_pipeline_step(sid, base_agent_config)
                return step_func(state)

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_steps)) as executor:
                future_to_step = {
                    executor.submit(_run_parallel_step, sid): sid
                    for sid in active_steps
                }
                for future in concurrent.futures.as_completed(future_to_step):
                    sid = future_to_step[future]
                    try:
                        step_results[sid] = future.result()
                    except Exception as e:
                        step_errors[sid] = e

            for sid in active_steps:
                if sid in step_results:
                    cast(dict, state).update(step_results[sid])
                    yield _emit_completed(sid, state)

            if step_errors:
                first_err_step = next(iter(step_errors))
                first_exc = step_errors[first_err_step]
                setattr(first_exc, "_partial_state", _state_snapshot(state))
                setattr(first_exc, "_failed_step", first_err_step)
                raise first_exc

        if state.get("_pipeline_stop_early"):
            reason = state.get("_pipeline_stop_reason") or ""
            if reason:
                yield {"agent": stage[0], "status": "warning", "message": reason}
            break

    _finalize_pipeline_machine(state, machine)
    return state
