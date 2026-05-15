from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.App.integrations.domain.postmortem import (
    Postmortem,
    new_postmortem_id,
    serialise_postmortem,
)
from backend.App.spec.domain.ports import VerificationFinding

_COLLECTION = "postmortems"


def _extract_summary(retry_history: list[Any], final_exception: Optional[Exception]) -> str:
    if final_exception is not None:
        return str(final_exception)[:500]
    if not retry_history:
        return "codegen failed with no retry history"
    last = retry_history[-1]
    attempt = getattr(last, "attempt", "?")
    count = getattr(last, "finding_count", 0)
    first_findings = getattr(last, "first_findings", ())
    first_msg = first_findings[0].message if first_findings else "no message"
    return (
        f"verification failed after {attempt} attempt(s): "
        f"{count} error(s); last: {first_msg}"
    )[:500]


def _extract_findings_excerpt(retry_history: list[Any]) -> tuple[str, ...]:
    if not retry_history:
        return ()
    last = retry_history[-1]
    first_findings: tuple[VerificationFinding, ...] = getattr(last, "first_findings", ())
    return tuple(f.message for f in first_findings[:3])


def _infer_failure_kind(
    retry_history: list[Any],
    final_exception: Optional[Exception],
) -> str:
    if final_exception is not None and not retry_history:
        return "exception"
    if retry_history:
        return "retry_exhausted"
    return "exception"


def record_codegen_failure(
    spec_id: str,
    agent: str,
    retry_history: list[Any],
    final_exception: Optional[Exception] = None,
) -> Postmortem:
    failure_kind = _infer_failure_kind(retry_history, final_exception)
    summary = _extract_summary(retry_history, final_exception)
    findings_excerpt = _extract_findings_excerpt(retry_history)

    recovery_parts: list[str] = []
    if retry_history:
        recovery_parts.append(f"{len(retry_history)} retry attempt(s) made")
    if final_exception is not None:
        recovery_parts.append("exception raised; no successful generation")
    recovery_attempted = "; ".join(recovery_parts) if recovery_parts else "none"

    return Postmortem(
        id=new_postmortem_id(),
        spec_id=spec_id,
        agent=agent,
        failure_kind=failure_kind,
        summary=summary,
        findings_excerpt=findings_excerpt,
        recovery_attempted=recovery_attempted,
        outcome="failed",
        recorded_at=datetime.now(tz=timezone.utc),
        tags=(spec_id, agent, failure_kind),
    )


def persist_postmortem(
    postmortem: Postmortem,
    vector_store: Any,
    embedding_provider: Any,
) -> None:
    text_to_embed = postmortem.summary
    if postmortem.findings_excerpt:
        text_to_embed += "\n" + "\n".join(postmortem.findings_excerpt)

    vector: list[float] = []
    if embedding_provider is not None:
        vectors = embedding_provider.embed([text_to_embed[:4000]])
        vector = list(vectors[0]) if vectors else []

    payload = serialise_postmortem(postmortem)
    vector_store.upsert(_COLLECTION, postmortem.id, vector, payload)


__all__ = [
    "persist_postmortem",
    "record_codegen_failure",
]
