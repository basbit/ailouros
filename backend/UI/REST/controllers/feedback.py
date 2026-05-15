from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from backend.App.integrations.domain.codegen_feedback import (
    CodegenFeedback,
    new_feedback_id,
    serialise_feedback,
)

router = APIRouter()

_VALID_VERDICTS = frozenset({"accept", "reject", "edit"})


class FeedbackBody(BaseModel):
    spec_id: str
    agent: str
    target_file: str
    verdict: str
    user_edit_diff: Optional[str] = None
    reason: Optional[str] = None
    tags: list[str] = []

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in _VALID_VERDICTS:
            raise ValueError(
                f"verdict must be one of {sorted(_VALID_VERDICTS)}, got {v!r}"
            )
        return v

    @field_validator("spec_id", "agent", "target_file")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v


@router.post("/v1/codegen-feedback")
def submit_feedback(body: FeedbackBody) -> dict:
    from backend.App.integrations.application.feedback_recorder import record_feedback
    from backend.App.integrations.infrastructure.qdrant_client import get_vector_store
    from backend.App.integrations.infrastructure.embedding_service import get_embedding_provider
    from typing import Literal, cast

    feedback = CodegenFeedback(
        id=new_feedback_id(),
        spec_id=body.spec_id,
        agent=body.agent,
        target_file=body.target_file,
        verdict=cast(Literal["accept", "reject", "edit"], body.verdict),
        user_edit_diff=body.user_edit_diff,
        reason=body.reason,
        recorded_at=datetime.now(tz=timezone.utc),
        tags=tuple(body.tags),
    )

    vector_store = get_vector_store()
    embedding_provider = get_embedding_provider()
    record_feedback(feedback, vector_store, embedding_provider)

    return {"id": feedback.id, "recorded_at": feedback.recorded_at.isoformat()}


@router.get("/v1/codegen-feedback")
def list_feedback(
    spec_id: str = Query(...),
    target_file: str = Query(...),
    k: int = Query(10, ge=1, le=100),
) -> dict:
    from backend.App.integrations.application.feedback_recorder import retrieve_feedback
    from backend.App.integrations.infrastructure.qdrant_client import get_vector_store
    from backend.App.integrations.infrastructure.embedding_service import get_embedding_provider

    if not spec_id.strip():
        raise HTTPException(status_code=400, detail="spec_id must not be blank")
    if not target_file.strip():
        raise HTTPException(status_code=400, detail="target_file must not be blank")

    vector_store = get_vector_store()
    embedding_provider = get_embedding_provider()
    items = retrieve_feedback(spec_id, target_file, vector_store, embedding_provider, k=k)

    return {
        "items": [serialise_feedback(fb) for fb in items],
        "count": len(items),
    }


__all__ = ["router"]
