"""Pipeline state initialisation, compaction, and human-resume helpers.

Extracted from pipeline_graph.py to keep that module under 500 lines.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional
from typing import cast

from backend.App.orchestration.application.pipeline_state import (
    ARTIFACT_AGENT_OUTPUT_KEYS,
    PipelineState,
    _PIPELINE_STRING_KEYS,
)
from backend.App.orchestration.application.nodes._shared import (
    _validate_tools_only_mcp_state,
    _warn_workspace_context_vs_custom_pipeline,
)

logger = logging.getLogger(__name__)

_ASSEMBLED_USER_TASK_MARKER = "\n\n---\n\n# User task\n\n"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _build_workspace_identity(
    *,
    workspace_root: str,
    workspace_root_resolved: str,
    project_manifest: str,
    workspace_snapshot: str,
) -> dict[str, str]:
    return {
        "workspace_root": str(workspace_root or "").strip(),
        "workspace_root_resolved": str(workspace_root_resolved or "").strip(),
        "project_manifest_hash": _sha256_text(str(project_manifest or "")),
        "workspace_snapshot_hash": _sha256_text(str(workspace_snapshot or "")),
    }


def _enforce_workspace_identity(
    *,
    workspace_root: str,
    workspace_root_resolved: str,
    pipeline_workspace_parts: Optional[dict[str, Any]],
) -> None:
    expected_root = str(workspace_root_resolved or "").strip()
    actual_root = str(workspace_root or "").strip()
    if expected_root and actual_root:
        try:
            if Path(expected_root).resolve() != Path(actual_root).resolve():
                raise ValueError(
                    "workspace identity mismatch: prepared workspace_root_resolved="
                    f"{Path(expected_root).resolve()} != runtime workspace_root={Path(actual_root).resolve()}"
                )
        except OSError:
            if expected_root != actual_root:
                raise ValueError(
                    "workspace identity mismatch: prepared workspace_root_resolved="
                    f"{expected_root!r} != runtime workspace_root={actual_root!r}"
                )
    if not isinstance(pipeline_workspace_parts, dict):
        return
    carried_identity = pipeline_workspace_parts.get("workspace_identity")
    if not isinstance(carried_identity, dict):
        return
    carried_root = str(carried_identity.get("workspace_root_resolved") or carried_identity.get("workspace_root") or "").strip()
    if carried_root and actual_root:
        try:
            if Path(carried_root).resolve() != Path(actual_root).resolve():
                raise ValueError(
                    "workspace identity mismatch: carried artifacts/cache belong to "
                    f"{Path(carried_root).resolve()} but runtime workspace_root is {Path(actual_root).resolve()}"
                )
        except OSError:
            if carried_root != actual_root:
                raise ValueError(
                    "workspace identity mismatch: carried artifacts/cache belong to "
                    f"{carried_root!r} but runtime workspace_root is {actual_root!r}"
                )


def _legacy_workspace_parts_from_input(user_input: str) -> dict[str, str]:
    """Если ``input`` собран ``build_input_with_workspace``, выделяем user_task."""
    if _ASSEMBLED_USER_TASK_MARKER not in user_input:
        return {
            "user_task": user_input.strip(),
            "project_manifest": "",
            "workspace_snapshot": "",
            "workspace_context_mode": "full",
            "workspace_section_title": "Workspace snapshot",
        }
    head, _, tail = user_input.partition(_ASSEMBLED_USER_TASK_MARKER)
    return {
        "user_task": tail.strip() if tail.strip() else user_input.strip(),
        "project_manifest": "",
        "workspace_snapshot": "",
        "workspace_context_mode": "full",
        "workspace_section_title": "Workspace snapshot",
    }


def _set_feature_env(
    swarm_cfg: dict[str, Any],
    cfg_key: str,
    env_key: str,
    *,
    is_str: bool = False,
) -> None:
    """Set an env var from agent_config.swarm if the value is set.

    For boolean flags (is_str=False): truthy values → "1", falsy → "0".
    For string overrides (is_str=True): non-empty string is set as-is.
    """
    val = swarm_cfg.get(cfg_key)
    if val is None:
        return
    if is_str:
        str_val = str(val).strip()
        if str_val:
            os.environ[env_key] = str_val
    else:
        os.environ[env_key] = "1" if val and str(val).strip() not in ("0", "false", "False") else "0"


def _initial_pipeline_state(
    user_input: str,
    agent_config: dict[str, Any],
    *,
    workspace_root: str = "",
    workspace_apply_writes: bool = False,
    task_id: str = "",
    cancel_event: Optional[threading.Event] = None,
    pipeline_workspace_parts: Optional[dict[str, Any]] = None,
    pipeline_step_ids: Optional[list[str]] = None,
) -> PipelineState:
    from backend.App.integrations.infrastructure.doc_fetch import run_doc_fetch_if_enabled
    from backend.App.integrations.infrastructure.mcp.auto.auto import apply_auto_mcp_to_agent_config
    from backend.App.workspace.infrastructure.workspace_io import normalize_workspace_context_mode

    effective_agent_config = apply_auto_mcp_to_agent_config(
        copy.deepcopy(agent_config or {}),
        workspace_root=workspace_root or "",
    )
    doc_manifest = run_doc_fetch_if_enabled(
        effective_agent_config,
        workspace_root=workspace_root or "",
        task_id=(task_id or "").strip(),
    )
    workspace_context_mcp_fallback = False
    if pipeline_workspace_parts and isinstance(pipeline_workspace_parts, dict):
        parts = pipeline_workspace_parts
        user_task = str(parts.get("user_task") or "").strip() or user_input.strip()
        project_manifest = str(parts.get("project_manifest") or "")
        workspace_snapshot = str(parts.get("workspace_snapshot") or "")
        workspace_root_resolved = str(parts.get("workspace_root_resolved") or workspace_root or "").strip()
        workspace_context_mode = normalize_workspace_context_mode(str(parts.get("workspace_context_mode") or "full"))
        workspace_section_title = str(parts.get("workspace_section_title") or "Workspace snapshot").strip() or "Workspace snapshot"
        workspace_context_mcp_fallback = bool(parts.get("workspace_context_mcp_fallback"))
    else:
        legacy_parts = _legacy_workspace_parts_from_input(user_input)
        user_task = legacy_parts["user_task"]
        project_manifest = legacy_parts["project_manifest"]
        workspace_snapshot = legacy_parts["workspace_snapshot"]
        workspace_root_resolved = str(workspace_root or "").strip()
        workspace_context_mode = legacy_parts["workspace_context_mode"]
        workspace_section_title = legacy_parts["workspace_section_title"]

    _enforce_workspace_identity(
        workspace_root=workspace_root,
        workspace_root_resolved=workspace_root_resolved,
        pipeline_workspace_parts=pipeline_workspace_parts,
    )
    workspace_identity = _build_workspace_identity(
        workspace_root=workspace_root,
        workspace_root_resolved=workspace_root_resolved,
        project_manifest=project_manifest,
        workspace_snapshot=workspace_snapshot,
    )

    initial_state: dict[str, Any] = {
        "input": user_input,
        "user_task": user_task,
        "project_manifest": project_manifest,
        "workspace_snapshot": workspace_snapshot,
        "workspace_context_mode": workspace_context_mode,
        "workspace_section_title": workspace_section_title,
        "workspace_context_mcp_fallback": workspace_context_mcp_fallback,
        "agent_config": effective_agent_config,
        "workspace_root": workspace_root,
        "workspace_root_resolved": workspace_root_resolved,
        "workspace_identity": workspace_identity,
        "workspace_apply_writes": workspace_apply_writes,
        "task_id": (task_id or "").strip(),
        "code_analysis": {},
        "doc_fetch_manifest": doc_manifest,
    }
    for k in _PIPELINE_STRING_KEYS:
        initial_state[k] = ""
    initial_state["dev_qa_tasks"] = []
    initial_state["dev_task_outputs"] = []
    initial_state["qa_task_outputs"] = []
    initial_state["dev_mcp_write_actions"] = []
    initial_state["pm_memory_artifact"] = {}
    initial_state["ba_memory_artifact"] = {}
    initial_state["arch_memory_artifact"] = {}
    initial_state["spec_memory_artifact"] = {}
    initial_state["clarify_input_cache"] = {}
    initial_state["planning_review_feedback"] = {}
    initial_state["planning_review_blockers"] = []
    initial_state["pipeline_phase"] = "PLAN"
    initial_state["pipeline_machine"] = {}
    initial_state["deliverables_artifact"] = {}
    initial_state["must_exist_files"] = []
    initial_state["spec_symbols"] = []
    initial_state["production_paths"] = []
    initial_state["placeholder_allow_list"] = []
    initial_state["arch_repo_evidence"] = []
    initial_state["arch_unverified_claims"] = []
    initial_state["devops_repo_evidence"] = []
    initial_state["devops_unverified_claims"] = []
    initial_state["problem_spotter_repo_evidence"] = []
    initial_state["problem_spotter_unverified_claims"] = []
    initial_state["refactor_plan_repo_evidence"] = []
    initial_state["refactor_plan_unverified_claims"] = []
    initial_state["dev_manifest"] = {}
    initial_state["verification_contract"] = {}
    initial_state["verification_gates"] = []
    initial_state["pipeline_metrics"] = {}
    initial_state["deliverable_write_mapping"] = []
    initial_state["dev_defect_report"] = {}
    initial_state["qa_defect_report"] = {}
    initial_state["qa_review_defect_report"] = {}
    initial_state["open_defects"] = []
    initial_state["clustered_open_defects"] = []
    initial_state["dev_subtask_contracts"] = []
    initial_state["ba_repo_evidence"] = []
    initial_state["ba_unverified_claims"] = []
    if cancel_event is not None:
        initial_state["_pipeline_cancel_event"] = cancel_event
    # K-1 / K-11: propagate UI-driven feature flags to env vars so that
    # self_verify.py and deep_planning.py (which read env at module level) can be
    # re-read after each pipeline initialisation via their own accessors.
    swarm_cfg = effective_agent_config.get("swarm") or {}
    _set_feature_env(swarm_cfg, "self_verify", "SWARM_SELF_VERIFY")
    _set_feature_env(swarm_cfg, "self_verify_model", "SWARM_SELF_VERIFY_MODEL", is_str=True)
    _set_feature_env(swarm_cfg, "deep_planning", "SWARM_DEEP_PLANNING")
    _set_feature_env(swarm_cfg, "deep_planning_model", "SWARM_DEEP_PLANNING_MODEL", is_str=True)
    # EC-3b: wire UI toggles to env (auto_approve, auto_retry, dream)
    _set_feature_env(swarm_cfg, "auto_approve", "SWARM_AUTO_APPROVE")
    _set_feature_env(swarm_cfg, "auto_approve_timeout", "SWARM_AUTO_APPROVE_TIMEOUT_SECONDS", is_str=True)
    _set_feature_env(swarm_cfg, "auto_retry", "SWARM_AUTO_RETRY_ON_NEEDS_WORK")
    _set_feature_env(swarm_cfg, "max_step_retries", "SWARM_MAX_STEP_RETRIES", is_str=True)
    _set_feature_env(swarm_cfg, "dream_enabled", "SWARM_DREAM_ENABLED")
    _set_feature_env(swarm_cfg, "background_agent", "SWARM_BACKGROUND_AGENT")

    # Propagate runtime context to env for agents that read env directly (e.g. PMAgent)
    if workspace_root:
        os.environ["SWARM_WORKSPACE_ROOT"] = workspace_root
    if task_id:
        os.environ["SWARM_CURRENT_TASK_ID"] = task_id.strip()

    _validate_tools_only_mcp_state(cast(PipelineState, initial_state))
    _warn_workspace_context_vs_custom_pipeline(cast(PipelineState, initial_state), pipeline_step_ids)
    return cast(PipelineState, initial_state)


HUMAN_PIPELINE_STEP_TO_STATE_KEY: dict[str, str] = {
    step_id: output_key
    for step_id, output_key in ARTIFACT_AGENT_OUTPUT_KEYS
    if step_id.startswith("human_")
}
# Старые UI / pipeline_steps: human_pm_tasks → те же поля, что и dev_lead
HUMAN_PIPELINE_STEP_TO_STATE_KEY["human_pm_tasks"] = "dev_lead_human_output"

# Runtime-only keys that must not be serialised or deep-copied.
_RUNTIME_STATE_KEYS = ("_pipeline_cancel_event", "_current_step_id")


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *state* with unpicklable runtime keys stripped."""
    clean = {k: v for k, v in state.items() if k not in _RUNTIME_STATE_KEYS}
    return copy.deepcopy(clean)


# ---------------------------------------------------------------------------
# C-1: State size compaction
# ---------------------------------------------------------------------------

def _state_max_chars() -> int:
    raw = os.getenv("SWARM_STATE_MAX_CHARS", "").strip()
    if raw:
        try:
            return max(10000, int(raw))
        except ValueError:
            pass
    return 200_000


# Keys to preserve during compaction (planning / task spec essentials).
_COMPACTION_KEEP_KEYS: frozenset[str] = frozenset({
    "input", "user_task", "project_manifest", "workspace_context_mode",
    "workspace_section_title", "workspace_context_mcp_fallback",
    "workspace_root", "workspace_apply_writes", "task_id",
    "workspace_root_resolved", "workspace_identity",
    "agent_config", "doc_fetch_manifest",
    # Merge/spec outputs are load-bearing for Dev/QA steps
    "spec_output", "arch_output", "ba_output",
    # Dev/QA results
    "dev_output", "dev_qa_tasks", "dev_task_outputs", "qa_task_outputs",
})

# Keys containing large outputs from review/human steps that are safe to summarise.
_COMPACTION_SUMMARISE_KEYS: frozenset[str] = frozenset(
    key for _, key in ARTIFACT_AGENT_OUTPUT_KEYS
    if key not in _COMPACTION_KEEP_KEYS
)


def _compact_state_if_needed(
    state: dict[str, Any],
    current_step: str,
) -> Optional[dict[str, Any]]:
    """Compact state if it exceeds SWARM_STATE_MAX_CHARS.

    Returns a dict suitable for yielding as a pipeline event, or None if
    no compaction was needed.
    """
    import json as _json_mod

    limit = _state_max_chars()
    try:
        serialised = _json_mod.dumps(
            {k: v for k, v in state.items() if k not in _RUNTIME_STATE_KEYS},
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:
        logger.warning("_compact_state_if_needed: failed to serialise state: %s", exc)
        return None

    before_chars = len(serialised)
    if before_chars <= limit:
        return None

    dropped: list[str] = []
    # Try LLM summarization if enabled, otherwise plain truncation
    from backend.App.orchestration.application.state_summarizer import (
        state_summarize_enabled,
        summarize_text,
    )
    _use_llm_summary = state_summarize_enabled()
    _agent_config = state.get("agent_config") if isinstance(state.get("agent_config"), dict) else None

    for key in sorted(_COMPACTION_SUMMARISE_KEYS):
        val = state.get(key)
        if isinstance(val, str) and len(val) > 200:
            if _use_llm_summary:
                state[key] = summarize_text(val, role_hint=key, agent_config=_agent_config)
            else:
                state[key] = val[:200] + " … [compacted]"
            dropped.append(key)

    # Second pass: if still over limit, truncate large keep-keys too
    try:
        after_first = len(_json_mod.dumps(
            {k: v for k, v in state.items() if k not in _RUNTIME_STATE_KEYS},
            ensure_ascii=False, default=str,
        ))
    except Exception as exc:
        logger.debug("_compact_state_if_needed: failed to measure after first pass: %s", exc)
        after_first = before_chars
    if after_first > limit:
        _AGGRESSIVE_TRUNCATE_LIMIT = 20_000
        for key in ("input", "code_analysis", "spec_output", "arch_output", "ba_output"):
            val = state.get(key)
            if isinstance(val, str) and len(val) > _AGGRESSIVE_TRUNCATE_LIMIT:
                state[key] = val[:_AGGRESSIVE_TRUNCATE_LIMIT] + " … [aggressively compacted]"
                dropped.append(key)
            elif isinstance(val, dict):
                try:
                    s = _json_mod.dumps(val, ensure_ascii=False, default=str)
                    if len(s) > _AGGRESSIVE_TRUNCATE_LIMIT:
                        state[key] = s[:_AGGRESSIVE_TRUNCATE_LIMIT] + " … [aggressively compacted]"
                        dropped.append(key)
                except Exception as exc:
                    logger.debug("_compact_state_if_needed: failed to serialize key %s: %s", key, exc)

    try:
        after_chars = len(_json_mod.dumps(
            {k: v for k, v in state.items() if k not in _RUNTIME_STATE_KEYS},
            ensure_ascii=False,
            default=str,
        ))
    except Exception as exc:
        logger.debug("_compact_state_if_needed: failed to measure compacted state size: %s", exc)
        after_chars = before_chars

    logger.warning(
        "state_compacted: step=%s before_chars=%d after_chars=%d limit=%d dropped_keys=%s",
        current_step,
        before_chars,
        after_chars,
        limit,
        dropped,
    )
    return {
        "agent": current_step,
        "status": "progress",
        "message": (
            f"[state_compacted] before={before_chars} after={after_chars} "
            f"limit={limit} dropped={dropped}"
        ),
        "_state_compacted": {
            "type": "state_compacted",
            "step_id": current_step,
            "before_chars": before_chars,
            "after_chars": after_chars,
            "limit": limit,
            "dropped_keys": dropped,
        },
    }


def _migrate_legacy_pm_tasks_state(state: dict[str, Any]) -> None:
    """Снимки до переименования pm_tasks → dev_lead."""
    pairs: tuple[tuple[str, str], ...] = (
        ("pm_tasks_output", "dev_lead_output"),
        ("pm_tasks_model", "dev_lead_model"),
        ("pm_tasks_provider", "dev_lead_provider"),
        ("pm_tasks_review_output", "dev_lead_review_output"),
        ("pm_tasks_review_model", "dev_lead_review_model"),
        ("pm_tasks_review_provider", "dev_lead_review_provider"),
        ("pm_tasks_human_output", "dev_lead_human_output"),
    )
    for old, new in pairs:
        new_value = state.get(new)
        is_empty = new_value is None or (isinstance(new_value, str) and not new_value.strip())
        old_value = state.get(old)
        if is_empty and isinstance(old_value, str) and old_value.strip():
            state[new] = old_value


def human_pipeline_step_label(pipeline_step_id: str) -> str:
    if pipeline_step_id in ("human_pm_tasks", "human_dev_lead"):
        return "dev_lead"
    if pipeline_step_id == "human_code_review":
        return "code_review"
    return pipeline_step_id.removeprefix("human_")


def format_human_resume_output(pipeline_step_id: str, feedback: str) -> str:
    """Текст, который попадает в *_human_output при ручном продолжении."""
    label = human_pipeline_step_label(pipeline_step_id)
    body = (feedback or "").strip()
    if not body:
        return f"[human:{label}] Confirmed manually (no edits)."
    # Structured logging for clarify_input answers (Q1: ...\nQ2: ... format)
    if pipeline_step_id in ("human_clarify_input", "clarify_input") and body.startswith("Q"):
        return f"[human:{label}] Answers received:\n{body}"
    return f"[human:{label}] Operator edits:\n{body}"


# ---- K-9: Quality gate helpers ----

def increment_step_retry(state: dict, step_id: str) -> dict:
    """Increment retry counter for a step; returns updated state dict."""
    retries = dict(state.get("step_retries") or {})
    retries[step_id] = retries.get(step_id, 0) + 1
    return {**state, "step_retries": retries}


def get_step_retries(state: dict, step_id: str) -> int:
    """Return current retry count for a step."""
    return int((state.get("step_retries") or {}).get(step_id, 0))


def append_step_feedback(state: dict, step_id: str, feedback: str) -> dict:
    """Append reviewer feedback for a step; returns updated state dict."""
    step_feedback = dict(state.get("step_feedback") or {})
    existing = list(step_feedback.get(step_id) or [])
    existing.append(feedback)
    step_feedback[step_id] = existing
    return {**state, "step_feedback": step_feedback}
