from __future__ import annotations

from typing import Any

from backend.App.integrations.domain.postmortem import (
    Postmortem,
    PostmortemQuery,
    parse_postmortem,
)

_COLLECTION = "postmortems"


def retrieve_postmortems(
    query: PostmortemQuery,
    vector_store: Any,
    embedding_provider: Any,
    query_text: str,
) -> tuple[Postmortem, ...]:
    query_vector: list[float] = []
    if embedding_provider is not None and query_text.strip():
        vectors = embedding_provider.embed([query_text[:4000]])
        query_vector = list(vectors[0]) if vectors else []

    k = max(1, min(50, query.k))

    if query_vector:
        hits = vector_store.search(_COLLECTION, query_vector, limit=k * 4)
    else:
        hits = vector_store.scroll(_COLLECTION, limit=k * 4)

    results: list[Postmortem] = []
    for hit in hits:
        payload = hit.payload
        if not isinstance(payload, dict):
            continue
        if query.spec_id is not None and payload.get("spec_id") != query.spec_id:
            continue
        if query.agent is not None and payload.get("agent") != query.agent:
            continue
        if query.failure_kind is not None and payload.get("failure_kind") != query.failure_kind:
            continue
        if query.tag is not None:
            tags = list(payload.get("tags") or [])
            if query.tag not in tags:
                continue
        try:
            postmortem = parse_postmortem(payload)
        except (ValueError, KeyError):
            continue
        results.append(postmortem)
        if len(results) >= k:
            break

    return tuple(results)


def format_postmortems_for_prompt(postmortems: tuple[Postmortem, ...]) -> str:
    if not postmortems:
        return ""
    lines = ["[past failures to avoid]"]
    for pm in postmortems:
        lines.append(f"- {pm.summary}")
        lines.append(f"  recovery: {pm.recovery_attempted}")
    return "\n".join(lines)


__all__ = [
    "format_postmortems_for_prompt",
    "retrieve_postmortems",
]
