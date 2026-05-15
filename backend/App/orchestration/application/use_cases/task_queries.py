from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.App.shared.application.runtime_telemetry import build_runtime_telemetry


def clarify_questions_payload(
    task_id: str,
    task_data: dict[str, Any],
    artifacts_root: Path,
) -> dict[str, Any]:
    from backend.App.orchestration.infrastructure.human_approval import pending_human_context
    from backend.App.orchestration.application.nodes.clarify_parser import parse_clarify_questions

    context = pending_human_context(task_id)
    if not context or "NEEDS_CLARIFICATION" not in context:
        return {"task_id": task_id, "questions": []}

    questions = parse_clarify_questions(context)
    return {
        "task_id": task_id,
        "questions": [
            {"index": q.index, "text": q.text, "options": q.options}
            for q in questions
        ],
    }


def task_metrics_payload(task_id: str) -> dict[str, Any]:
    try:
        from backend.App.integrations.infrastructure.observability.step_metrics import snapshot_for_task

        return snapshot_for_task(task_id)
    except Exception:
        return {"task_id": task_id, "steps": []}


def runtime_telemetry_payload(task_id: str, artifacts_root: Path) -> dict[str, Any]:
    task_dir = artifacts_root / task_id
    runtime_path = task_dir / "runtime.json"
    if runtime_path.is_file():
        try:
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            return {
                key: payload[key]
                for key in ("context_mode", "tools_enabled", "mcp_phase")
                if key in payload
            }
    snapshot = _read_pipeline_snapshot(task_dir)
    if not snapshot:
        return {}
    partial_state = snapshot.get("partial_state")
    state = partial_state if isinstance(partial_state, dict) else snapshot
    agent_config = (
        state.get("agent_config")
        if isinstance(state.get("agent_config"), dict)
        else {}
    )
    workspace_meta = {
        "workspace_context_mode": state.get("workspace_context_mode"),
        "workspace_context_mcp_fallback": state.get("workspace_context_mcp_fallback"),
    }
    return build_runtime_telemetry(agent_config, workspace_meta)


_RESUMABLE_TASK_STATUSES = frozenset({"failed", "cancelled", "awaiting_human"})


def compute_resume_options(
    task_id: str,
    task_data: dict[str, Any],
    artifacts_root: Path,
) -> dict[str, Any]:
    status = str(task_data.get("status") or "").strip().lower()
    task_dir = artifacts_root / task_id
    snapshot = _read_pipeline_snapshot(task_dir)
    pipeline_path_exists = (task_dir / "pipeline.json").is_file()
    base = {
        "task_id": task_id,
        "can_resume": False,
        "resume_step": "",
        "reason": "not_resumable",
        "task_status": status,
        "pipeline_snapshot_present": pipeline_path_exists,
    }
    if not snapshot:
        return base
    clarification = snapshot.get("clarification_pause")
    if isinstance(clarification, dict):
        step_id = str(clarification.get("step_id") or "")
        if step_id:
            base.update(
                can_resume=True,
                resume_step=step_id,
                reason="clarification_pause",
            )
            return base
    resume_step = str(snapshot.get("resume_from_step") or "").strip()
    failed_step = str(snapshot.get("failed_step") or "").strip()
    if resume_step:
        partial = snapshot.get("partial_state")
        base.update(
            can_resume=isinstance(partial, dict) and bool(partial),
            resume_step=resume_step,
            reason="human_gate" if status == "awaiting_human" else "partial_state",
        )
        return base
    if failed_step:
        base.update(
            can_resume=status in _RESUMABLE_TASK_STATUSES,
            resume_step=failed_step,
            reason="failed_step",
        )
        return base
    if status in _RESUMABLE_TASK_STATUSES and snapshot.get("partial_state"):
        return base | {
            "can_resume": True,
            "resume_step": "",
            "reason": "partial_state_no_step",
        }
    return base


def _read_pipeline_snapshot(task_dir: Path) -> dict[str, Any]:
    pipeline_path = task_dir / "pipeline.json"
    if not pipeline_path.is_file():
        return {}
    try:
        payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
