"""Load wiki context from .swarm/wiki/ for injection into agent prompts.

Two retrieval modes:

* **Flat dump** (default, used when no ``query`` is supplied or semantic
  search is disabled) — reads ``index.md`` plus the first few articles
  under priority sub-directories. Backwards-compatible with the original
  loader; kept so that pipeline-startup wiring that has no task query
  still gets a useful overview block.

* **Semantic** (used when a non-empty ``query`` is supplied) — delegates
  to :mod:`workspace.application.wiki_searcher`, which embeds wiki
  paragraphs once and returns the top-k chunks. Falls back to token
  overlap when embeddings are unavailable. Significantly tighter than
  the flat dump (typically 1.5–2× fewer characters injected per step
  with higher precision).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Maximum chars to inject per article (flat-dump mode)
_MAX_INDEX_CHARS = 2000
_MAX_ARTICLE_CHARS = 1500
_MAX_TOTAL_CHARS = 12000  # raised: 8000 was cutting off architecture + planning articles

# Priority sub-directories to scan for relevant articles (pipeline-generated + user-created)
_PRIORITY_DIRS = (
    "architecture", "planning", "development", "analysis",
    "documentation", "qa", "devops",
    "features", "api", "agents",
    "design",  # UX/UI articles written by design agents
)


def _read_file(path: Path, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text
    except OSError:
        return ""


def _flat_dump(wiki_root: Path) -> str:
    parts: list[str] = []
    total = 0

    # 1. index.md — high-level overview
    index_path = wiki_root / "index.md"
    if index_path.exists():
        content = _read_file(index_path, _MAX_INDEX_CHARS)
        if content:
            parts.append(f"## index\n{content}")
            total += len(content)

    # 2. Priority articles from architecture/, features/, api/
    for sub in _PRIORITY_DIRS:
        if total >= _MAX_TOTAL_CHARS:
            break
        sub_dir = wiki_root / sub
        if not sub_dir.is_dir():
            continue
        for md in sorted(sub_dir.glob("*.md"))[:4]:
            if total >= _MAX_TOTAL_CHARS:
                break
            content = _read_file(md, _MAX_ARTICLE_CHARS)
            if content:
                rel = md.relative_to(wiki_root).with_suffix("").as_posix()
                parts.append(f"## {rel}\n{content}")
                total += len(content)

    if not parts:
        return ""

    logger.debug("wiki_context_loader: flat dump %d chars from %s", total, wiki_root)
    return "\n\n".join(parts)


def load_wiki_context(
    workspace_root: str | Path,
    *,
    query: Optional[str] = None,
    max_chars: Optional[int] = None,
) -> str:
    """Return wiki memory block for ``<workspace_root>/.swarm/wiki/``.

    Parameters
    ----------
    workspace_root:
        Project root that owns the ``.swarm/wiki`` directory.
    query:
        Optional textual query (task description, current step output,
        defect summary…). When non-empty and semantic search is enabled,
        the loader performs paragraph-level retrieval via
        :mod:`wiki_searcher`. When ``None`` or empty, falls back to the
        legacy flat dump.
    max_chars:
        Optional cap on the rendered block (semantic mode only). When
        omitted, ``SWARM_WIKI_SEARCH_MAX_CHARS`` is honoured.

    Returns an empty string if the wiki doesn't exist or has no content.
    """
    wiki_root = Path(workspace_root) / ".swarm" / "wiki"
    if not wiki_root.exists():
        return ""

    if query and query.strip():
        try:
            from backend.App.workspace.application.wiki_searcher import (
                search_block,
                wiki_search_enabled,
            )
        except ImportError:
            # Defensive: keep the legacy path if the searcher module is
            # ever absent (e.g. partial install). Logged at debug to avoid
            # noisy warnings on unrelated workflows.
            logger.debug("wiki_context_loader: wiki_searcher unavailable, using flat dump")
        else:
            if wiki_search_enabled():
                semantic_block = search_block(wiki_root, query, max_chars=max_chars)
                if semantic_block:
                    return semantic_block
                # No hits → fall through to flat dump so the agent at
                # least sees the overview rather than nothing.

    return _flat_dump(wiki_root)


# ---------------------------------------------------------------------------
# Query helper for pipeline callers
# ---------------------------------------------------------------------------


# Step → "what does this agent care about" hint. Concatenated with the
# user task so the embedding model picks up role-specific signal even when
# the task itself is short.
_STEP_HINTS: dict[str, str] = {
    "pm": "product management goals scope deliverables",
    "ba": "business analyst requirements user stories acceptance criteria",
    "architect": "architecture system design technology stack components",
    "spec_merge": "specification merge consolidation",
    "dev_lead": "implementation plan task breakdown engineering",
    "dev": "code implementation modules functions",
    "qa": "quality assurance testing validation defects",
    "devops": "deployment infrastructure pipeline release",
    "documentation": "documentation diagrams readme",
    "design": "user interface user experience visual design",
    "marketing": "marketing seo positioning audience",
    "problem_spotter": "issues defects gaps risks problems",
    "refactor_plan": "refactoring restructuring cleanup",
}

# Output fields that carry the most informative previous-step text per step.
# Order matters: we take the first non-empty one.
_QUERY_SOURCES_PER_STEP: dict[str, tuple[str, ...]] = {
    "pm": ("clarify_input_human_output", "user_task"),
    "ba": ("pm_output", "user_task"),
    "architect": ("ba_output", "pm_output", "user_task"),
    "spec_merge": ("arch_output", "ba_output", "user_task"),
    "dev_lead": ("spec_output", "arch_output", "user_task"),
    "dev": ("dev_lead_output", "spec_output", "user_task"),
    "qa": ("dev_output", "spec_output", "user_task"),
    "devops": ("spec_output", "user_task"),
    "documentation": ("dev_output", "spec_output", "user_task"),
    "design": ("ba_output", "user_task"),
    "marketing": ("dev_output", "spec_output", "user_task"),
    "problem_spotter": ("arch_output", "spec_output", "user_task"),
    "refactor_plan": ("problem_spotter_output", "user_task"),
}


_DEFAULT_QUERY_SOURCES: tuple[str, ...] = ("user_task",)
_QUERY_MAX_CHARS = 1200  # keeps prompts short for embedding APIs


def query_for_pipeline_step(
    state: Mapping[str, Any] | None, step_id: str
) -> str:
    """Build a compact query string for semantic wiki retrieval.

    Picks the most informative previous-step output (or user task) for
    *step_id*, prepends a short role hint, and trims to
    :data:`_QUERY_MAX_CHARS`. Returns an empty string when the state has
    nothing useful — callers will then get the legacy flat dump.
    """
    if not isinstance(state, Mapping):
        return ""

    sources = _QUERY_SOURCES_PER_STEP.get(step_id, _DEFAULT_QUERY_SOURCES)
    body = ""
    for key in sources:
        value = str(state.get(key) or "").strip()
        if value:
            body = value
            break

    if not body:
        # Last-resort: any non-empty top-level *_output that's not a marker.
        for key, value in state.items():
            if not key.endswith("_output") or not isinstance(value, str):
                continue
            value = value.strip()
            if value:
                body = value
                break

    if not body:
        return ""

    hint = _STEP_HINTS.get(step_id, "")
    query = f"{hint}\n{body}".strip() if hint else body
    if len(query) > _QUERY_MAX_CHARS:
        query = query[:_QUERY_MAX_CHARS]
    return query
