from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_BACKUP_DIRNAME = ".swarm_backups"
_MANIFEST_NAME = "changes_manifest.json"


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    sha256_before: str
    sha256_after: str | None
    size_before: int
    size_after: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256_before": self.sha256_before,
            "sha256_after": self.sha256_after,
            "size_before": self.size_before,
            "size_after": self.size_after,
        }


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes | None:
    try:
        if not path.is_file():
            return None
        return path.read_bytes()
    except OSError:
        return None


def _ensure_backup_root(workspace_root: Path) -> Path:
    root = workspace_root / _BACKUP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_versioned(workspace_root: Path) -> bool:
    return (workspace_root / ".git").exists() or (workspace_root / ".hg").exists()


def snapshot_before_writes(
    workspace_root: Path,
    relative_paths: Iterable[str],
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    backup_root = _ensure_backup_root(workspace_root)
    run_dir = backup_root / time.strftime("run-%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    for relative in relative_paths:
        clean = (relative or "").strip().lstrip("/").lstrip("\\")
        if not clean:
            continue
        target = workspace_root / clean
        data = _read_bytes(target)
        if data is None:
            snapshots[clean] = {
                "sha256_before": "",
                "size_before": 0,
                "existed_before": False,
                "backup_path": None,
            }
            continue
        backup_target = run_dir / clean
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(target, backup_target)
        except OSError as exc:
            logger.warning("workspace_backup: copy failed for %s: %s", clean, exc)
            backup_target = None  # type: ignore[assignment]
        snapshots[clean] = {
            "sha256_before": _hash_bytes(data),
            "size_before": len(data),
            "existed_before": True,
            "backup_path": (
                str(backup_target.relative_to(workspace_root))
                if backup_target is not None else None
            ),
        }
    snapshots["_run_dir"] = {"path": str(run_dir.relative_to(workspace_root))}
    return snapshots


def finalize_change_manifest(
    workspace_root: Path,
    snapshots: dict[str, dict[str, Any]],
    written_paths: Iterable[str],
) -> dict[str, Any]:
    manifest_entries: list[FileSnapshot] = []
    written_clean = sorted({
        (path or "").strip().lstrip("/").lstrip("\\")
        for path in written_paths
        if (path or "").strip()
    })
    snapshot_keys = [k for k in snapshots if not k.startswith("_")]
    union = sorted(set(snapshot_keys) | set(written_clean))
    for relative in union:
        before = snapshots.get(relative, {})
        target = workspace_root / relative
        after_bytes = _read_bytes(target)
        manifest_entries.append(
            FileSnapshot(
                relative_path=relative,
                sha256_before=str(before.get("sha256_before") or ""),
                sha256_after=_hash_bytes(after_bytes) if after_bytes is not None else None,
                size_before=int(before.get("size_before") or 0),
                size_after=len(after_bytes) if after_bytes is not None else None,
            )
        )
    backup_root = _ensure_backup_root(workspace_root)
    run_dir_meta = snapshots.get("_run_dir", {})
    run_dir_rel = str(run_dir_meta.get("path") or _BACKUP_DIRNAME)
    manifest_path = workspace_root / run_dir_rel / _MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_dir": run_dir_rel,
        "is_versioned": is_versioned(workspace_root),
        "entries": [entry.to_dict() for entry in manifest_entries],
    }
    try:
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("workspace_backup: manifest write failed: %s", exc)
    payload["manifest_path"] = str(manifest_path.relative_to(workspace_root))
    payload["backup_root"] = str(backup_root.relative_to(workspace_root))
    return payload
