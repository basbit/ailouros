from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from backend.App.workspace.domain.ports import FileEntry, ReadResult, WorkspaceIOPort

logger = logging.getLogger(__name__)


class FsApiAdapter(WorkspaceIOPort):
    def __init__(self, workspace_root: str | Path, *, allow_write: bool = False) -> None:
        self._root = Path(workspace_root).resolve()
        self._allow_write = allow_write

    def _safe_resolve(self, path: str) -> Path:
        if not path or path in (".", "/"):
            return self._root
        candidate = (self._root / path).resolve()
        if not str(candidate).startswith(str(self._root)):
            raise ValueError(
                f"Path traversal detected: {path!r} resolves outside workspace root {self._root}"
            )
        return candidate

    def list(
        self,
        path: str = "",
        *,
        max_depth: int = 3,
        max_files: int = 500,
    ) -> list[FileEntry]:
        base = self._safe_resolve(path)
        if not base.is_dir():
            return []

        entries: list[FileEntry] = []
        root_depth = len(base.parts)

        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            depth = len(p.parts) - root_depth
            if depth > max_depth:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            rel = p.relative_to(self._root).as_posix()
            entries.append(FileEntry(path=rel, size_bytes=size))
            if len(entries) >= max_files:
                break

        logger.info(
            "workspace_fs: op=list path=%r entries=%d max_depth=%d",
            path or ".",
            len(entries),
            max_depth,
        )
        return entries

    def read(self, path: str, *, max_chars: int = 50_000) -> ReadResult:
        resolved = self._safe_resolve(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"workspace file not found: {path!r}")

        try:
            raw_bytes = resolved.read_bytes()
        except OSError as exc:
            raise OSError(f"workspace read error for {path!r}: {exc}") from exc

        original_bytes = len(raw_bytes)
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"workspace decode error for {path!r}: {exc}") from exc

        truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            truncated = True

        logger.info(
            "workspace_fs: op=read path=%r chars=%d truncated=%s original_bytes=%d",
            path,
            len(text),
            truncated,
            original_bytes,
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
        self._safe_resolve(path)
        try:
            result = subprocess.run(
                ["git", "diff", from_ref, to_ref, "--", path],
                capture_output=True,
                text=True,
                cwd=str(self._root),
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise RuntimeError(f"workspace diff failed for {path!r}: {exc}") from exc

        diff_text = result.stdout
        truncated = False
        if len(diff_text) > max_chars:
            diff_text = diff_text[:max_chars] + "\n… [diff truncated]"
            truncated = True

        logger.info(
            "workspace_fs: op=diff path=%r from=%r to=%r chars=%d truncated=%s",
            path,
            from_ref,
            to_ref,
            len(diff_text),
            truncated,
        )
        return diff_text

    def write(self, path: str, content: str) -> None:
        if not self._allow_write:
            raise PermissionError(
                f"workspace write denied for {path!r}: FsApiAdapter was created with allow_write=False"
            )
        resolved = self._safe_resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info(
            "workspace_fs: op=write path=%r chars=%d",
            path,
            len(content),
        )
