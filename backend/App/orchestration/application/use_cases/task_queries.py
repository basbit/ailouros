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


def _read_pipeline_snapshot(task_dir: Path) -> dict[str, Any]:
    pipeline_path = task_dir / "pipeline.json"
    if not pipeline_path.is_file():
        return {}
    try:
        payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
