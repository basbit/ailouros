"""Auto-update .swarm/wiki/ after a pipeline run completes."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _format_workspace_identity(raw: Any) -> str:
    """Render the ``workspace_identity`` pipeline-state value as a string.

    ``PipelineState.workspace_identity`` is declared as
    :class:`WorkspaceIdentityState` (a ``TypedDict``), but legacy snapshots
    persisted it as a plain string.  Both shapes must render deterministically
    into the wiki index.  Unknown shapes fail explicitly (§2 of review rules).
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        # Sort keys for reproducible output (§6 determinism).
        return json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False)
    raise TypeError(
        f"workspace_identity has unexpected type {type(raw).__name__!r}; "
        "expected str, dict, or None."
    )


def _feature_name(file_path: str) -> str | None:
    """Extract feature name from a frontend/src/features/<X>/... path."""
    parts = file_path.replace("\\", "/").split("/")
    try:
        idx = parts.index("features")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return None


def _stub_content(title: str, tags: list[str]) -> str:
    tags_str = ", ".join(f'"{t}"' for t in tags)
    return (
        f"---\ntitle: {title}\ntags: [{tags_str}]\nlinks: []\n---\n\n"
        "_Auto-created. Add details._\n"
    )


def _upsert_stub(path: Path, title: str, tags: list[str]) -> None:
    """Create *path* with a stub if it doesn't already exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_stub_content(title, tags), encoding="utf-8")


def _write_session_article(wiki_root: Path, state: dict[str, Any]) -> None:
    import os

    task_input: str = state.get("input", "") or ""
    files_changed: list[str] = []
    diff = state.get("dev_workspace_diff") or {}
    if isinstance(diff, dict):
        files_changed = list(diff.get("files_changed") or [])

    today = date.today().isoformat()
    sessions_dir = wiki_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / f"{today}-task.md"

    # Build stub body (used as fallback when agent is off or raises)
    task_summary = (task_input[:200] + "...") if len(task_input) > 200 else task_input
    files_str = "\n".join(f"- `{f}`" for f in files_changed[:20]) or "_none_"
    stub_body = (
        f"---\ntitle: Session {today}\ntags: [\"log\", \"session\"]\nlinks: []\n---\n\n"
        f"## Task\n\n{task_summary}\n\n"
        f"## Files Changed\n\n{files_str}\n\n"
        f"**Date:** {today}\n"
    )

    # env: SWARM_WIKI_WRITER
    wiki_writer_enabled = os.environ.get("SWARM_WIKI_WRITER", "0").strip() == "1"
    if wiki_writer_enabled:
        existing_content = session_path.read_text(encoding="utf-8") if session_path.exists() else ""
        try:
            from backend.App.orchestration.infrastructure.agents.wiki_writer_agent import WikiWriterAgent
            agent = WikiWriterAgent()
            body = agent.write_article(
                step_id="session",
                step_output=task_input[:3000],
                existing_content=existing_content,
            )
        except Exception:
            logger.warning(
                "WikiWriterAgent failed for session article on %s — falling back to stub",
                today,
                exc_info=True,
            )
            body = stub_body
    else:
        body = stub_body

    session_path.write_text(body, encoding="utf-8")


def _upsert_feature_articles(wiki_root: Path, files_changed: list[str]) -> None:
    seen_features: set[str] = set()
    for fp in files_changed:
        normalised = fp.replace("\\", "/")
        if "frontend/src/features/" in normalised:
            feat = _feature_name(normalised)
            if feat and feat not in seen_features:
                seen_features.add(feat)
                article = wiki_root / "features" / f"{feat}.md"
                _upsert_stub(article, title=feat, tags=["feature"])
        elif "backend/App/orchestration/" in normalised:
            _upsert_stub(
                wiki_root / "architecture" / "pipeline.md",
                title="Pipeline Architecture",
                tags=["architecture"],
            )
        elif "backend/UI/REST/controllers/" in normalised:
            _upsert_stub(
                wiki_root / "api" / "endpoints.md",
                title="API Endpoints",
                tags=["api"],
            )


def _update_index(wiki_root: Path, state: dict[str, Any]) -> None:
    """Regenerate index.md with a snapshot of known wiki articles.

    Only creates/updates the index — does not touch individual articles.
    """
    index_path = wiki_root / "index.md"

    # Collect all .md files except index itself
    articles: list[str] = []
    for md in sorted(wiki_root.rglob("*.md")):
        if md == index_path:
            continue
        rel = md.relative_to(wiki_root).with_suffix("").as_posix()
        articles.append(rel)

    if not articles:
        return

    # Read frontmatter title from each article for the index listing
    lines: list[str] = [
        "---",
        'title: "Project Wiki Index"',
        'tags: ["index"]',
        "links: []",
        "---",
        "",
        "# Project Wiki",
        "",
        "_Auto-generated index. Updated after each pipeline run._",
        "",
        "## Articles",
        "",
    ]
    for rel in articles:
        lines.append(f"- [[{rel}]]")

    # Append workspace identity if available (accepts legacy str or TypedDict).
    identity = _format_workspace_identity(state.get("workspace_identity"))
    if identity:
        lines += ["", "## Workspace", "", f"```\n{identity[:500]}\n```"]

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_wiki_from_pipeline(state: dict[str, Any], workspace_root: Path) -> None:
    """Create/update wiki articles in ``<workspace_root>/.swarm/wiki/``.

    Wiki update is a side-effect of the pipeline that the user has already
    received the result for — so a failure here must never tear the pipeline
    down (§7 separation of concerns: wiki is observability, not domain).

    However, every failure is reported with a full traceback (``logger.exception``)
    instead of a one-line warning — operators must be able to diagnose drift
    (§2: errors must be actionable).

    Set ``SWARM_WIKI_AUTOUPDATE_STRICT=1`` to escalate failures into raised
    exceptions for tests and CI environments.
    """
    import os

    strict = os.environ.get("SWARM_WIKI_AUTOUPDATE_STRICT", "0").strip() == "1"
    try:
        wiki_root = workspace_root / ".swarm" / "wiki"
        wiki_root.mkdir(parents=True, exist_ok=True)

        _write_session_article(wiki_root, state)

        diff = state.get("dev_workspace_diff") or {}
        files_changed: list[str] = []
        if isinstance(diff, dict):
            files_changed = list(diff.get("files_changed") or [])

        _upsert_feature_articles(wiki_root, files_changed)

        # Regenerate index.md so agents can load a fresh overview next run
        _update_index(wiki_root, state)

        from backend.App.workspace.application.wiki_service import get_or_build_graph
        get_or_build_graph(wiki_root)

    except (OSError, ValueError, TypeError, RuntimeError, ImportError):
        # Specific exception types — narrower than the original BLE001 catch.
        # We still avoid bare ``Exception`` so programming errors (NameError,
        # AttributeError, KeyError on developer typos) propagate normally.
        logger.exception("wiki auto-update failed")
        if strict:
            raise
