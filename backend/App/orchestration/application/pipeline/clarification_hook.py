from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.App.orchestration.application.nodes.clarify_parser import (
    ClarifyQuestion,
    parse_clarify_questions,
)

_MEDIA_ROLES_DEFAULT: frozenset[str] = frozenset(
    {
        "image_generator",
        "audio_generator",
        "asset_fetcher",
        "media_generator",
        "visual_probe",
    }
)


def _role_allows_clarification(role: str, role_cfg: Mapping[str, Any]) -> bool:
    explicit = role_cfg.get("can_request_clarification")
    if isinstance(explicit, bool):
        return explicit
    return role not in _MEDIA_ROLES_DEFAULT


def maybe_pause_for_clarification(
    step_id: str,
    output: str,
    state: Any,
    role_cfg: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    if not isinstance(output, str) or "NEEDS_CLARIFICATION" not in output:
        return None
    if role_cfg is None:
        role_cfg = {}
    if not _role_allows_clarification(step_id, role_cfg):
        return None
    questions = parse_clarify_questions(output)
    if not questions:
        return None
    return {
        "step_id": step_id,
        "reason": "needs_clarification",
        "questions": [
            {"index": question.index, "text": question.text, "options": question.options}
            for question in questions
        ],
    }


__all__ = [
    "ClarifyQuestion",
    "maybe_pause_for_clarification",
    "parse_clarify_questions",
]
