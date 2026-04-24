from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkspaceDiffResult:
    diff_text: str
    files_changed: list[str]
    stats: dict[str, int]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "diff_text": self.diff_text,
            "files_changed": self.files_changed,
            "stats": self.stats,
            "source": self.source,
        }


@dataclass(frozen=True)
class WorkspaceFileResult:
    path: str
    content: str


@dataclass(frozen=True)
class WorkspacePatchResult:
    ok: bool
    path: str


def _empty_diff() -> WorkspaceDiffResult:
    return WorkspaceDiffResult(
        diff_text="",
        files_changed=[],
        stats={"added": 0, "removed": 0, "files": 0},
        source="none",
    )


def _task_payload(task_id: str, task_store: Any) -> dict[str, Any]:
    payload = task_store.get_task(task_id)
    return payload if isinstance(payload, dict) else {}


def _pipeline_payload(task_id: str, artifacts_root: Path) -> dict[str, Any]:
    path = artifacts_root / task_id / "pipeline.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _workspace_root_from_pipeline(data: dict[str, Any]) -> Path:
    partial = data.get("partial_state")
    workspace = data.get("workspace")
    candidates = [
        data.get("workspace_root"),
        partial.get("workspace_root") if isinstance(partial, dict) else None,
        workspace.get("workspace_root_resolved") if isinstance(workspace, dict) else None,
        workspace.get("workspace_root") if isinstance(workspace, dict) else None,
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return Path(text).expanduser().resolve()
    raise ValueError("workspace_root is missing for this task")


def _resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    rel = str(relative_path or "").strip()
    if not rel:
        raise ValueError("path must not be empty")
    if Path(rel).is_absolute():
        raise ValueError("path must be relative to workspace_root")
    target = (workspace_root / rel).resolve()
    if target != workspace_root and workspace_root not in target.parents:
        raise ValueError("path escapes workspace_root")
    return target


def get_task_workspace_diff(
    *,
    task_id: str,
    task_store: Any,
    artifacts_root: Path,
) -> WorkspaceDiffResult:
    _task_payload(task_id, task_store)
    pipeline = _pipeline_payload(task_id, artifacts_root)
    diff = pipeline.get("dev_workspace_diff")
    if not isinstance(diff, dict):
        return _empty_diff()
    _stats = diff.get("stats")
    stats: dict[str, Any] = _stats if isinstance(_stats, dict) else {}
    return WorkspaceDiffResult(
        diff_text=str(diff.get("diff_text") or ""),
        files_changed=[str(p) for p in diff.get("files_changed") or []],
        stats={
            "added": int(stats.get("added") or 0),
            "removed": int(stats.get("removed") or 0),
            "files": int(stats.get("files") or 0),
        },
        source=str(diff.get("source") or "unknown"),
    )


def read_task_workspace_file(
    *,
    task_id: str,
    relative_path: str,
    task_store: Any,
    artifacts_root: Path,
) -> WorkspaceFileResult:
    _task_payload(task_id, task_store)
    root = _workspace_root_from_pipeline(_pipeline_payload(task_id, artifacts_root))
    target = _resolve_workspace_path(root, relative_path)
    content = target.read_text(encoding="utf-8")
    return WorkspaceFileResult(path=str(relative_path), content=content)


def patch_task_workspace_file(
    *,
    task_id: str,
    relative_path: str,
    content: str,
    task_store: Any,
    artifacts_root: Path,
) -> WorkspacePatchResult:
    if os.getenv("SWARM_ALLOW_WORKSPACE_WRITE", "0") != "1":
        raise PermissionError("workspace writes are disabled")
    _task_payload(task_id, task_store)
    root = _workspace_root_from_pipeline(_pipeline_payload(task_id, artifacts_root))
    target = _resolve_workspace_path(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return WorkspacePatchResult(ok=True, path=str(relative_path))
