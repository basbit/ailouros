from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from backend.App.shared.infrastructure.activity_recorder import record as record_activity
from backend.App.spec.application.document_graph_service import (
    build_workspace_graph,
    docs_root_for,
    write_graph,
)

_SLUG_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_segment(value: str) -> str:
    cleaned = _SLUG_PATTERN.sub("_", value.strip())
    return cleaned or "_"


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _spec_hash_for(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _frontmatter_yaml(
    *,
    spec_id: str,
    agent: str,
    step_id: str,
    version: int,
    depends_on: Iterable[str],
    produces: Iterable[str],
    spec_hash: str,
) -> str:
    lines = [
        f"spec_id: {spec_id}",
        f"agent: {agent}",
        f"step_id: {step_id}",
        f"version: {version}",
    ]
    lines.append("depends_on:" if depends_on else "depends_on: []")
    for value in depends_on:
        lines.append(f"  - {value}")
    lines.append("produces:" if produces else "produces: []")
    for value in produces:
        lines.append(f"  - {value}")
    lines.append(f"spec_hash: {spec_hash}")
    lines.append(f"recorded_at: {_iso_now()}")
    return "\n".join(lines)


def _agent_dir(workspace_root: Path, agent: str) -> Path:
    return docs_root_for(workspace_root) / _safe_segment(agent)


def _doc_path(workspace_root: Path, agent: str, step_id: str) -> Path:
    return _agent_dir(workspace_root, agent) / f"{_safe_segment(step_id)}.md"


def _archive_path(workspace_root: Path, agent: str, step_id: str, version: int) -> Path:
    return _agent_dir(workspace_root, agent) / "_archive" / f"{_safe_segment(step_id)}.v{version}.md"


def _read_existing_version(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([0-9]+)\s*$", text, re.MULTILINE)
    if match is None:
        return 0
    return int(match.group(1))


def write_document(
    workspace_root: Path,
    *,
    agent: str,
    step_id: str,
    spec_id: str,
    body: str,
    depends_on: Optional[Iterable[str]] = None,
    produces: Optional[Iterable[str]] = None,
) -> Path:
    if not workspace_root or not isinstance(workspace_root, Path):
        raise ValueError("workspace_root must be a pathlib.Path")
    if not agent.strip() or not step_id.strip() or not spec_id.strip():
        raise ValueError("agent, step_id, spec_id must all be non-empty")
    target = _doc_path(workspace_root, agent, step_id)
    previous_version = _read_existing_version(target)
    if previous_version >= 1:
        archive_target = _archive_path(workspace_root, agent, step_id, previous_version)
        archive_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, archive_target)
    next_version = previous_version + 1 if previous_version > 0 else 1
    cleaned_depends = tuple(sorted({v.strip() for v in (depends_on or ()) if v.strip()}))
    cleaned_produces = tuple(sorted({v.strip() for v in (produces or ()) if v.strip()}))
    body_text = body if body.endswith("\n") else body + "\n"
    spec_hash = _spec_hash_for(body_text)
    frontmatter = _frontmatter_yaml(
        spec_id=spec_id.strip(),
        agent=agent.strip(),
        step_id=step_id.strip(),
        version=next_version,
        depends_on=cleaned_depends,
        produces=cleaned_produces,
        spec_hash=spec_hash,
    )
    contents = f"---\n{frontmatter}\n---\n{body_text}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    graph = build_workspace_graph(workspace_root)
    write_graph(workspace_root, graph)
    record_activity(
        "doc_ops",
        {
            "op": "write",
            "agent": agent,
            "step_id": step_id,
            "spec_id": spec_id,
            "version": next_version,
            "depends_on": list(cleaned_depends),
            "produces": list(cleaned_produces),
            "path": str(target.relative_to(docs_root_for(workspace_root))),
        },
    )
    return target


def list_archived_versions(
    workspace_root: Path, *, agent: str, step_id: str
) -> list[Path]:
    archive_dir = _agent_dir(workspace_root, agent) / "_archive"
    if not archive_dir.is_dir():
        return []
    prefix = f"{_safe_segment(step_id)}.v"
    items = [
        path
        for path in archive_dir.iterdir()
        if path.is_file() and path.name.startswith(prefix) and path.suffix == ".md"
    ]
    items.sort()
    return items


__all__ = [
    "list_archived_versions",
    "write_document",
]
