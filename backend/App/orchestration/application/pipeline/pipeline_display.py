
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def pipeline_step_in_progress_message(step_id: str, state: Mapping[str, Any]) -> str:
    from backend.App.orchestration.application.routing.pipeline_graph import PIPELINE_STEP_REGISTRY

    row = PIPELINE_STEP_REGISTRY.get(step_id)
    base = row[0] if row else step_id
    if step_id == "dev":
        raw_roles = (state.get("agent_config") or {}).get("dev_roles")
        dev_roles = [r for r in (raw_roles or []) if isinstance(r, dict) and r.get("name")]
        if dev_roles:
            names = ", ".join(str(r.get("name")) for r in dev_roles)
            return f"{base} (roles: {names}; each role is a separate sequential LLM call)"
        raw = state.get("dev_qa_tasks") or []
        if isinstance(raw, list) and len(raw) > 1:
            n = len(raw)
            return (
                f"{base} ({n} subtasks in sequence, each with the full spec in the prompt — "
                f"slow; log: pipeline dev subtask start/done)"
            )
    if step_id == "qa":
        raw = state.get("dev_qa_tasks") or []
        if isinstance(raw, list) and len(raw) > 1:
            n = len(raw)
            return f"{base} ({n} QA subtasks in sequence; no intermediate SSE events)"
    return base
