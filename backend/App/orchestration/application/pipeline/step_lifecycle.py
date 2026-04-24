from __future__ import annotations

import logging
from typing import Any, Optional

from backend.App.shared.application.trace_emitter import emit_trace_child_event, emit_trace_event

_logger = logging.getLogger(__name__)


def register_step_start_with_contract_validator(task_id: str, step_id: str) -> None:
    if not task_id:
        return
    try:
        from backend.App.orchestration.domain.contract_validator import get_validator
        get_validator().step_start(task_id, step_id)
    except Exception:
        pass


def register_step_complete_with_contract_validator(task_id: str, step_id: str) -> None:
    if not task_id:
        return
    try:
        from backend.App.orchestration.domain.contract_validator import get_validator
        get_validator().step_complete(task_id, step_id)
    except Exception:
        pass


def register_step_error_with_contract_validator(task_id: str, step_id: str, error_message: str) -> None:
    if not task_id:
        return
    try:
        from backend.App.orchestration.domain.contract_validator import get_validator
        get_validator().step_error(task_id, step_id, error_message[:200])
    except Exception:
        pass


def emit_step_start_trace(
    trace_collector: Any,
    task_id: str,
    session_id: str,
    step_id: str,
) -> Optional[str]:
    if trace_collector is None or not session_id:
        return None
    try:
        return emit_trace_event(trace_collector, task_id, session_id, step_id, "step_start", {})
    except Exception:
        return None


def emit_step_end_trace(
    trace_collector: Any,
    task_id: str,
    session_id: str,
    step_id: str,
    step_event_id: Optional[str],
) -> None:
    if trace_collector is None or not session_id:
        return
    try:
        emit_trace_child_event(trace_collector, task_id, session_id, step_id, "step_end", step_event_id, {})
    except Exception:
        pass


def emit_step_error_trace(
    trace_collector: Any,
    task_id: str,
    session_id: str,
    step_id: str,
    step_event_id: Optional[str],
    error_message: str,
) -> None:
    if trace_collector is None or not session_id:
        return
    try:
        emit_trace_child_event(
            trace_collector, task_id, session_id, step_id, "error",
            step_event_id, {"error": error_message[:500]},
        )
    except Exception:
        pass


def checkpoint_session(session_manager: Any, session_id: str, step_id: str, task_id: str) -> None:
    if session_manager is None or not session_id:
        return
    try:
        session_manager.checkpoint(session_id, step_id, {"step": step_id, "task_id": task_id})
    except Exception as checkpoint_error:
        _logger.debug("Session checkpoint failed after %s: %s", step_id, checkpoint_error)


def register_dev_step_artifacts(state: Any, step_id: str) -> None:
    if step_id != "dev":
        return
    try:
        from backend.App.workspace.application.artifact_registry import register_step_artifacts
        workspace_writes_raw = state.get("workspace_writes") or {}
        workspace_writes: dict[str, Any] = workspace_writes_raw if isinstance(workspace_writes_raw, dict) else {}
        written_files = list(workspace_writes.get("written") or []) + list(workspace_writes.get("patched") or [])
        if written_files:
            register_step_artifacts(state, "dev", written_files, purpose="dev output")
    except Exception:
        pass


def index_step_state(state: Any) -> None:
    try:
        from backend.App.orchestration.application.context.state_searcher import index_state
        index_state(state)
    except Exception:
        pass


def mark_task_done_with_contract_validator(task_id: str) -> None:
    if not task_id:
        return
    try:
        from backend.App.orchestration.infrastructure.runtime_policy import get_runtime_validator
        get_runtime_validator().transition_task(task_id, "DONE")
    except Exception as validator_error:
        _logger.warning("ContractValidator: could not mark task %s as DONE: %s", task_id, validator_error)


def complete_session(session_manager: Any, session_id: Optional[str]) -> None:
    if session_manager is None or not session_id:
        return
    try:
        session_manager.complete_session(session_id)
    except Exception as complete_error:
        _logger.debug("Session complete failed: %s", complete_error)
