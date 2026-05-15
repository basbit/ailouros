from __future__ import annotations

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from backend.App.orchestration.application.pipeline.clarification_hook import (
    maybe_pause_for_clarification,
)
from backend.App.orchestration.application.snapshot_serializer import (
    pipeline_snapshot_for_disk,
)
from backend.App.shared.infrastructure.openai_sse import (
    build_done,
    build_extra_event,
    ensure_task_dirs,
)
from backend.App.tasks.infrastructure.task_run_log import append_task_run_log

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClarificationQuestion:
    index: int
    text: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class PauseDecision:
    step_id: str
    questions: tuple[ClarificationQuestion, ...]
    resume_payload: dict[str, Any]


def _role_cfg_from_snapshot(
    step_id: str, pipeline_snapshot: Mapping[str, Any]
) -> Mapping[str, Any]:
    agent_config = pipeline_snapshot.get("agent_config")
    if not isinstance(agent_config, dict):
        return {}
    role_cfg = agent_config.get(step_id)
    if isinstance(role_cfg, dict):
        return role_cfg
    return {}


def evaluate_step_clarification(
    step_id: str,
    output: str,
    pipeline_snapshot: Mapping[str, Any],
) -> Optional[PauseDecision]:
    role_cfg = _role_cfg_from_snapshot(step_id, pipeline_snapshot)
    pause = maybe_pause_for_clarification(step_id, output, pipeline_snapshot, role_cfg)
    if pause is None:
        return None
    questions = tuple(
        ClarificationQuestion(
            index=int(question["index"]),
            text=str(question["text"]),
            options=tuple(str(option) for option in question.get("options") or []),
        )
        for question in pause.get("questions") or []
    )
    if not questions:
        return None
    resume_payload = {
        "step_id": step_id,
        "reason": "needs_clarification",
        "questions": [
            {
                "index": question.index,
                "text": question.text,
                "options": list(question.options),
            }
            for question in questions
        ],
    }
    return PauseDecision(
        step_id=step_id,
        questions=questions,
        resume_payload=resume_payload,
    )


def _partial_state_from_snapshot(
    pipeline_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for key, value in pipeline_snapshot.items():
        if key in (
            "error",
            "partial_state",
            "resume_from_step",
            "human_approval_step",
            "clarification_pause",
        ):
            continue
        state[key] = value
    return state


def handle_step_clarification(
    *,
    step_id: str,
    output: str,
    pipeline_snapshot: dict[str, Any],
    task_store: Any,
    task_id: str,
    task_dir: Path,
    agents_dir: Path,
    now: int,
    request_model: str,
) -> Optional[PauseDecision]:
    decision = evaluate_step_clarification(step_id, output, pipeline_snapshot)
    if decision is None:
        return None
    questions_payload = decision.resume_payload["questions"]
    pause_record = {
        "step_id": decision.step_id,
        "reason": "needs_clarification",
        "questions": questions_payload,
    }
    pipeline_snapshot["clarification_pause"] = pause_record
    pipeline_snapshot["human_approval_step"] = decision.step_id
    pipeline_snapshot["resume_from_step"] = decision.step_id
    pipeline_snapshot["partial_state"] = _partial_state_from_snapshot(pipeline_snapshot)
    message = (
        f"[{decision.step_id}] awaiting clarification "
        f"({len(questions_payload)} question(s))"
    )
    task_store.update_task(
        task_id,
        status="awaiting_human",
        agent=decision.step_id,
        message=message,
    )
    append_task_run_log(task_dir, f"WAIT awaiting_clarification {decision.step_id}: {message}")
    try:
        ensure_task_dirs(task_dir, agents_dir)
        (task_dir / "pipeline.json").write_text(
            json.dumps(
                pipeline_snapshot_for_disk(pipeline_snapshot),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as io_error:
        logger.warning("clarification_pause pipeline.json write failed: %s", io_error)
    return decision


def emit_pause_events(
    decision: PauseDecision,
    *,
    now: int,
    request_model: str,
) -> Generator[str, None, None]:
    yield build_extra_event(
        now,
        request_model,
        awaiting_clarification={
            "type": "awaiting_clarification",
            "step_id": decision.step_id,
            "questions": [
                {
                    "index": question.index,
                    "text": question.text,
                    "options": list(question.options),
                }
                for question in decision.questions
            ],
        },
    )
    yield build_done(now, request_model)
    yield "data: [DONE]\n\n"


__all__ = [
    "ClarificationQuestion",
    "PauseDecision",
    "emit_pause_events",
    "evaluate_step_clarification",
    "handle_step_clarification",
]
