
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.App.workspace.domain.ports import (
    FileEntry,
    WorkspaceContextMode,
    WorkspaceContextPolicy,
    WorkspaceReaderPort,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceContextResult:

    snapshot: str
    context_mode: WorkspaceContextMode
    stats: dict[str, Any] = field(default_factory=dict)
    fallback_applied: bool = False
    fallback_reason: str = ""


@dataclass
class BuildWorkspaceContextConfig:

    max_depth: int = 3
    max_files: int = 500
    max_chars_per_file: int = 50_000
    max_snapshot_chars: int = 200_000
    priority_paths: list[str] = field(default_factory=list)


_DEFAULT_CONFIG = BuildWorkspaceContextConfig()


class BuildWorkspaceContextUseCase:

    def __init__(
        self,
        workspace_io: WorkspaceReaderPort,
        config: Optional[BuildWorkspaceContextConfig] = None,
    ) -> None:
        self._io = workspace_io
        self._config = config or _DEFAULT_CONFIG

    def execute(
        self,
        workspace_root: str,
        context_mode: WorkspaceContextMode,
        *,
        priority_paths: Optional[list[str]] = None,
        mcp_available: bool = True,
    ) -> WorkspaceContextResult:
        effective_mode = context_mode
        fallback_applied = False
        fallback_reason = ""

        if context_mode == WorkspaceContextMode.RETRIEVE_MCP and not mcp_available:
            policy = WorkspaceContextPolicy()
            if policy.is_valid_transition(context_mode, WorkspaceContextMode.RETRIEVE_FS):
                effective_mode = WorkspaceContextMode.RETRIEVE_FS
                fallback_applied = True
                fallback_reason = (
                    "RETRIEVE_MCP requested but MCP is unavailable; "
                    "falling back to RETRIEVE_FS (INV-1)"
                )
                logger.warning(
                    "BuildWorkspaceContextUseCase: %s workspace_root=%s",
                    fallback_reason,
                    workspace_root,
                )

        paths = priority_paths or self._config.priority_paths
        snapshot, stats = self._build_snapshot(workspace_root, effective_mode, paths)

        return WorkspaceContextResult(
            snapshot=snapshot,
            context_mode=effective_mode,
            stats=stats,
            fallback_applied=fallback_applied,
            fallback_reason=fallback_reason,
        )

    def _build_snapshot(
        self,
        workspace_root: str,
        mode: WorkspaceContextMode,
        priority_paths: list[str],
    ) -> tuple[str, dict[str, Any]]:
        cfg = self._config

        if mode == WorkspaceContextMode.RETRIEVE_MCP:
            snapshot = (
                f"# Workspace root: {workspace_root}\n\n"
                "No file contents inlined. Use MCP filesystem tools to read files.\n"
            )
            return snapshot, {"mode": mode.value, "files_included": 0}

        entries: list[FileEntry] = self._io.list(
            "",
            max_depth=cfg.max_depth,
            max_files=cfg.max_files,
        )

        if mode == WorkspaceContextMode.INDEX_ONLY:
            lines = [f"# Workspace root: {workspace_root}", f"# Files ({len(entries)}):"]
            lines += [f"  {e.path}" for e in entries]
            return "\n".join(lines) + "\n", {
                "mode": mode.value,
                "files_listed": len(entries),
                "files_included": 0,
            }

        if mode == WorkspaceContextMode.RETRIEVE_FS:
            lines = [f"# Workspace root: {workspace_root}", f"# File index ({len(entries)} files):"]
            lines += [f"  {e.path}" for e in entries]
            return "\n".join(lines) + "\n", {
                "mode": mode.value,
                "files_listed": len(entries),
                "files_included": 0,
            }

        if mode == WorkspaceContextMode.PRIORITY_PATHS:
            target_entries = [
                e for e in entries
                if any(e.path.startswith(p.lstrip("/")) for p in priority_paths)
            ] if priority_paths else entries
        else:
            target_entries = entries

        parts: list[str] = [f"# Workspace root: {workspace_root}\n"]
        chars_used = len(parts[0])
        files_included = 0

        for entry in target_entries:
            if chars_used >= cfg.max_snapshot_chars:
                parts.append(f"\n# ... snapshot limit reached ({cfg.max_snapshot_chars} chars)\n")
                break
            try:
                read_result = self._io.read(entry.path, max_chars=cfg.max_chars_per_file)
                block = f"\n## {entry.path}\n```\n{read_result.content}\n```\n"
                chars_used += len(block)
                parts.append(block)
                files_included += 1
            except Exception as exc:
                logger.warning(
                    "BuildWorkspaceContextUseCase: failed to read %s: %s", entry.path, exc
                )

        return "".join(parts), {
            "mode": mode.value,
            "files_listed": len(entries),
            "files_included": files_included,
            "chars_used": chars_used,
        }
