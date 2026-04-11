"""FsSnapshotAdapter — workspace adapter using orchestrator.workspace_io snapshot functions.

Wraps ``collect_workspace_snapshot`` and related functions for list/read
operations. Used as a secondary access path (retrieve + internal FS).

Old modules are NOT removed (Strangler Fig pattern).
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.App.workspace.domain.ports import FileEntry, ReadResult, WorkspaceIOPort

logger = logging.getLogger(__name__)


class FsSnapshotAdapter(WorkspaceIOPort):
    """Adapter over orchestrator workspace_io functions.

    Args:
        workspace_root: Absolute path to workspace directory.
        allow_write: If False (default), write() raises PermissionError.
    """

    def __init__(self, workspace_root: str | Path, *, allow_write: bool = False) -> None:
        from backend.App.workspace.infrastructure.workspace_io import validate_workspace_root
        self._root = validate_workspace_root(Path(workspace_root))
        self._allow_write = allow_write

    def list(
        self,
        path: str = "",
        *,
        max_depth: int = 3,
        max_files: int = 500,
    ) -> list[FileEntry]:
        from backend.App.workspace.infrastructure.workspace_io import collect_workspace_file_index

        text, count = collect_workspace_file_index(self._root, max_paths=max_files)
        entries: list[FileEntry] = []
        for line in text.splitlines():
            if not line.startswith("- "):
                continue
            parts = line[2:].split("\t")
            if len(parts) < 2:
                continue
            rel_path = parts[0].strip()
            try:
                size = int(parts[1].replace("bytes", "").strip())
            except ValueError:
                size = 0
            entries.append(FileEntry(path=rel_path, size_bytes=size))
        logger.info(
            "workspace_snapshot: op=list entries=%d root=%s",
            len(entries),
            self._root,
        )
        return entries

    def read(self, path: str, *, max_chars: int = 50_000) -> ReadResult:
        abs_path = (self._root / path).resolve()
        if not str(abs_path).startswith(str(self._root)):
            raise ValueError(f"Path traversal detected: {path!r}")
        if not abs_path.is_file():
            raise FileNotFoundError(f"workspace file not found: {path!r}")
        try:
            raw_bytes = abs_path.read_bytes()
        except OSError as exc:
            raise OSError(f"workspace read error: {exc}") from exc
        original_bytes = len(raw_bytes)
        text = raw_bytes.decode("utf-8", errors="replace")
        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True
        logger.info(
            "workspace_snapshot: op=read path=%r chars=%d truncated=%s",
            path,
            len(text),
            truncated,
        )
        return ReadResult(content=text, truncated=truncated, original_bytes=original_bytes)

    def diff(
        self,
        path: str,
        from_ref: str,
        to_ref: str,
        *,
        max_chars: int = 20_000,
    ) -> str:
        # Delegate to FsApiAdapter for git diff
        from backend.App.workspace.infrastructure.fs_api_adapter import FsApiAdapter
        return FsApiAdapter(self._root).diff(path, from_ref, to_ref, max_chars=max_chars)

    def write(self, path: str, content: str) -> None:
        if not self._allow_write:
            raise PermissionError(
                f"workspace write denied for {path!r}: FsSnapshotAdapter was created with allow_write=False"
            )
        abs_path = (self._root / path).resolve()
        if not str(abs_path).startswith(str(self._root)):
            raise ValueError(f"Path traversal detected: {path!r}")
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        logger.info("workspace_snapshot: op=write path=%r chars=%d", path, len(content))
