from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextChunk:
    relative_path: str
    body: str
    estimated_tokens: int
    priority: int


def _approximate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _read_text_safely(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def build_chunks(
    workspace_root: Path,
    candidate_paths: Iterable[str],
    priority_paths: Iterable[str] | None = None,
) -> list[ContextChunk]:
    priority_set = {
        (path or "").strip().lstrip("/")
        for path in (priority_paths or [])
        if (path or "").strip()
    }
    chunks: list[ContextChunk] = []
    for candidate in candidate_paths:
        relative = (candidate or "").strip().lstrip("/")
        if not relative:
            continue
        target = workspace_root / relative
        body = _read_text_safely(target)
        if body is None:
            continue
        priority = 0 if relative in priority_set else 1
        chunks.append(ContextChunk(
            relative_path=relative,
            body=body,
            estimated_tokens=_approximate_tokens(body),
            priority=priority,
        ))
    return chunks


def select_within_budget(
    chunks: list[ContextChunk],
    token_budget: int,
) -> list[ContextChunk]:
    if token_budget <= 0:
        return []
    ordered = sorted(chunks, key=lambda chunk: (chunk.priority, -chunk.estimated_tokens))
    selected: list[ContextChunk] = []
    used = 0
    for chunk in ordered:
        if used + chunk.estimated_tokens > token_budget:
            continue
        selected.append(chunk)
        used += chunk.estimated_tokens
    return selected


def render(chunks: list[ContextChunk]) -> str:
    lines: list[str] = []
    for chunk in chunks:
        lines.append(f"--- {chunk.relative_path} ---")
        lines.append(chunk.body)
        lines.append("")
    return "\n".join(lines).rstrip()


def assemble_context(
    workspace_root: Path,
    candidate_paths: Iterable[str],
    *,
    token_budget: int,
    priority_paths: Iterable[str] | None = None,
) -> dict[str, Any]:
    chunks = build_chunks(workspace_root, candidate_paths, priority_paths)
    selected = select_within_budget(chunks, token_budget)
    return {
        "rendered": render(selected),
        "selected_paths": [chunk.relative_path for chunk in selected],
        "skipped_paths": [
            chunk.relative_path for chunk in chunks if chunk not in selected
        ],
        "tokens_used": sum(chunk.estimated_tokens for chunk in selected),
        "tokens_budget": token_budget,
    }
