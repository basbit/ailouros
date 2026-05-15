from __future__ import annotations

from typing import Any

from backend.App.integrations.domain.codegen_feedback import (
    CodegenFeedback,
    parse_feedback,
    serialise_feedback,
)

_COLLECTION = "codegen_feedback"


def _build_embed_text(feedback: CodegenFeedback) -> str:
    parts = [
        f"verdict:{feedback.verdict}",
        f"file:{feedback.target_file}",
        f"spec:{feedback.spec_id}",
    ]
    if feedback.reason:
        parts.append(feedback.reason)
    if feedback.user_edit_diff:
        parts.append(feedback.user_edit_diff[:500])
    return " ".join(parts)


def record_feedback(
    feedback: CodegenFeedback,
    vector_store: Any,
    embedding_provider: Any,
) -> None:
    text = _build_embed_text(feedback)
    vector: list[float] = []
    if embedding_provider is not None:
        vectors = embedding_provider.embed([text[:4000]])
        vector = list(vectors[0]) if vectors else []

    payload = serialise_feedback(feedback)
    vector_store.upsert(_COLLECTION, feedback.id, vector, payload)


def retrieve_feedback(
    spec_id: str,
    target_file: str,
    vector_store: Any,
    embedding_provider: Any,
    k: int = 5,
) -> tuple[CodegenFeedback, ...]:
    query_text = f"spec:{spec_id} file:{target_file}"
    query_vector: list[float] = []
    if embedding_provider is not None and query_text.strip():
        vectors = embedding_provider.embed([query_text[:4000]])
        query_vector = list(vectors[0]) if vectors else []

    k_clamped = max(1, min(50, k))

    if query_vector:
        hits = vector_store.search(_COLLECTION, query_vector, limit=k_clamped * 4)
    else:
        hits = vector_store.scroll(_COLLECTION, limit=k_clamped * 4)

    results: list[CodegenFeedback] = []
    for hit in hits:
        payload = hit.payload
        if not isinstance(payload, dict):
            continue
        if payload.get("spec_id") != spec_id:
            continue
        if payload.get("target_file") != target_file:
            continue
        try:
            item = parse_feedback(payload)
        except (ValueError, KeyError):
            continue
        results.append(item)
        if len(results) >= k_clamped:
            break

    return tuple(results)


def format_feedback_for_prompt(items: tuple[CodegenFeedback, ...]) -> str:
    if not items:
        return ""
    lines = ["[past user feedback]"]
    for fb in items:
        line = f"- [{fb.verdict.upper()}] {fb.target_file}"
        if fb.reason:
            line += f": {fb.reason}"
        lines.append(line)
        if fb.user_edit_diff:
            lines.append(f"  diff: {fb.user_edit_diff[:300]}")
    return "\n".join(lines)


__all__ = [
    "format_feedback_for_prompt",
    "record_feedback",
    "retrieve_feedback",
]
