"""BuildWorkspaceContextUseCase — build a workspace context snapshot for agents.

Provides a clean port-based interface for constructing workspace context
without direct os.walk or Redis access.

Rules:
- All file-system access goes through WorkspaceIOPort (INV-7).
- Any context mode fallback is logged explicitly (INV-1).
- No fastapi/redis/httpx/openai/anthropic/langgraph at module level.
"""

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


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class WorkspaceContextResult:
    """Result of BuildWorkspaceContextUseCase."""

    snapshot: str
    context_mode: WorkspaceContextMode
    stats: dict[str, Any] = field(default_factory=dict)
    fallback_applied: bool = False
    fallback_reason: str = ""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BuildWorkspaceContextConfig:
    """Configuration for workspace context construction."""

    max_depth: int = 3
    max_files: int = 500
    max_chars_per_file: int = 50_000
    max_snapshot_chars: int = 200_000
    priority_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = BuildWorkspaceContextConfig()


class BuildWorkspaceContextUseCase:
    """Build a workspace context snapshot for agent consumption.

    Uses WorkspaceIOPort exclusively for all file-system access.
    Respects context_mode to decide what to include:

    - RETRIEVE_MCP: only emit a stub (files fetched on demand via MCP).
    - RETRIEVE_FS: index of paths, no file bodies.
    - PRIORITY_PATHS: full bodies for priority_paths only.
    - INDEX_ONLY: just the path index.
    - FULL: all file contents up to snapshot limit.

    Fallback (INV-1): if a mode cannot be satisfied (e.g. RETRIEVE_MCP but MCP
    unavailable), downgrades one level and logs explicitly.
    """

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
        """Build and return the workspace context.

        Args:
            workspace_root: Absolute path to the project root.
            context_mode: Requested context retrieval mode.
            priority_paths: Paths to prioritize in PRIORITY_PATHS mode.
            mcp_available: Whether MCP tools are accessible to the agent.

        Returns:
            WorkspaceContextResult with snapshot text and applied mode.
        """
        effective_mode = context_mode
        fallback_applied = False
        fallback_reason = ""

        # Fallback: RETRIEVE_MCP → RETRIEVE_FS if MCP is unavailable (INV-1)
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        workspace_root: str,
        mode: WorkspaceContextMode,
        priority_paths: list[str],
    ) -> tuple[str, dict[str, Any]]:
        cfg = self._config

        if mode == WorkspaceContextMode.RETRIEVE_MCP:
            # Only a stub — agent reads files on demand via MCP
            snapshot = (
                f"# Workspace root: {workspace_root}\n\n"
                "No file contents inlined. Use MCP filesystem tools to read files.\n"
            )
            return snapshot, {"mode": mode.value, "files_included": 0}

        # List all files for index-based modes
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
            # Index only — no bodies (agent fetches via internal FS)
            lines = [f"# Workspace root: {workspace_root}", f"# File index ({len(entries)} files):"]
            lines += [f"  {e.path}" for e in entries]
            return "\n".join(lines) + "\n", {
                "mode": mode.value,
                "files_listed": len(entries),
                "files_included": 0,
            }

        # PRIORITY_PATHS or FULL — include file bodies
        if mode == WorkspaceContextMode.PRIORITY_PATHS:
            target_entries = [
                e for e in entries
                if any(e.path.startswith(p.lstrip("/")) for p in priority_paths)
            ] if priority_paths else entries
        else:  # FULL
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
