from __future__ import annotations

from pathlib import Path
from typing import Any


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
