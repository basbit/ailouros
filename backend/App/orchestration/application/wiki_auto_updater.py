"""Auto-update .swarm/wiki/ after a pipeline run completes."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
    task_input: str = state.get("input", "") or ""
    files_changed: list[str] = []
    diff = state.get("dev_workspace_diff") or {}
    if isinstance(diff, dict):
        files_changed = list(diff.get("files_changed") or [])

    today = date.today().isoformat()
    sessions_dir = wiki_root / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / f"{today}-task.md"

    # Build a short summary (append if file already exists for today)
    task_summary = (task_input[:200] + "...") if len(task_input) > 200 else task_input
    files_str = "\n".join(f"- `{f}`" for f in files_changed[:20]) or "_none_"
    body = (
        f"---\ntitle: Session {today}\ntags: [\"log\", \"session\"]\nlinks: []\n---\n\n"
        f"## Task\n\n{task_summary}\n\n"
        f"## Files Changed\n\n{files_str}\n\n"
        f"**Date:** {today}\n"
    )
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

    # Append workspace identity if available
    identity = (state.get("workspace_identity") or "").strip()
    if identity:
        lines += ["", "## Workspace", "", f"```\n{identity[:500]}\n```"]

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_wiki_from_pipeline(state: dict[str, Any], workspace_root: Path) -> None:
    """Create/update wiki articles in *workspace_root*/.swarm/wiki/ from pipeline *state*.

    Non-blocking: all errors are logged as warnings and never propagated.
    """
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

    except Exception as exc:  # noqa: BLE001 — intentionally broad; wiki update must never crash the pipeline
        logger.warning("wiki auto-update failed: %s", exc)
