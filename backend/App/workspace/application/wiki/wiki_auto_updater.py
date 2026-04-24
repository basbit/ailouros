from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _format_workspace_identity(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        return json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False)
    raise TypeError(
        f"workspace_identity has unexpected type {type(raw).__name__!r}; "
        "expected str, dict, or None."
    )


def _feature_name(file_path: str) -> str | None:
    parts = file_path.replace("\\", "/").split("/")
    try:
        index = parts.index("features")
        if index + 1 < len(parts):
            return parts[index + 1]
    except ValueError:
        pass
    return None


def _stub_content(title: str, tags: list[str]) -> str:
    tags_str = ", ".join(f'"{tag}"' for tag in tags)
    return (
        f"---\ntitle: {title}\ntags: [{tags_str}]\nlinks: []\n---\n\n"
        "_Auto-created. Add details._\n"
    )


def _upsert_stub(path: Path, title: str, tags: list[str]) -> None:
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

    task_summary = (task_input[:200] + "...") if len(task_input) > 200 else task_input
    files_str = "\n".join(f"- `{file}`" for file in files_changed[:20]) or "_none_"
    stub_body = (
        f"---\ntitle: Session {today}\ntags: [\"log\", \"session\"]\nlinks: []\n---\n\n"
        f"## Task\n\n{task_summary}\n\n"
        f"## Files Changed\n\n{files_str}\n\n"
        f"**Date:** {today}\n"
    )

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
    for file_path in files_changed:
        normalised = file_path.replace("\\", "/")
        if "frontend/src/features/" in normalised:
            feature = _feature_name(normalised)
            if feature and feature not in seen_features:
                seen_features.add(feature)
                article = wiki_root / "features" / f"{feature}.md"
                _upsert_stub(article, title=feature, tags=["feature"])
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
    index_path = wiki_root / "index.md"

    articles: list[str] = []
    for md_file in sorted(wiki_root.rglob("*.md")):
        if md_file == index_path:
            continue
        rel_path = md_file.relative_to(wiki_root).with_suffix("").as_posix()
        articles.append(rel_path)

    if not articles:
        return

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
    for rel_path in articles:
        lines.append(f"- [[{rel_path}]]")

    identity = _format_workspace_identity(state.get("workspace_identity"))
    if identity:
        lines += ["", "## Workspace", "", f"```\n{identity[:500]}\n```"]

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_wiki_from_pipeline(state: dict[str, Any], workspace_root: Path) -> None:
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
        _update_index(wiki_root, state)

        from backend.App.workspace.application.wiki_service import get_or_build_graph
        get_or_build_graph(wiki_root)

    except (OSError, ValueError, TypeError, RuntimeError, ImportError):
        logger.exception("wiki auto-update failed")
        if strict:
            raise
