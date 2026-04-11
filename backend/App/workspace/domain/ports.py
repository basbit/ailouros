"""Workspace domain ports and value objects.

Rules (INV-7): this module MUST NOT import fastapi, redis, httpx, openai,
anthropic, langgraph, or subprocess. Only stdlib + typing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

class WorkspaceContextMode(str, Enum):
    """Workspace context-retrieval modes in priority order (highest first)."""
    RETRIEVE_MCP = "retrieve_mcp"
    RETRIEVE_FS = "retrieve_fs"
    PRIORITY_PATHS = "priority_paths"
    INDEX_ONLY = "index_only"
    FULL = "full"


# Orchestrator-layer mode string constants (kept in domain so application/orchestration
# BCs can import them without depending on workspace infrastructure).
WORKSPACE_CONTEXT_MODE_DEFAULT: str = "retrieve"
WORKSPACE_CONTEXT_MODE_TOOLS_ONLY: str = "tools_only"


@dataclass(frozen=True)
class FileEntry:
    path: str       # relative POSIX path within workspace root
    size_bytes: int


@dataclass(frozen=True)
class ReadResult:
    content: str
    truncated: bool
    original_bytes: int


# ---------------------------------------------------------------------------
# Domain policies
# ---------------------------------------------------------------------------

class WorkspaceContextPolicy:
    """Validates context-mode transitions (INV-1: no silent fallbacks).

    Priority order (highest to lowest):
      1. RETRIEVE_MCP
      2. RETRIEVE_FS
      3. PRIORITY_PATHS
      4. INDEX_ONLY
      5. FULL  (least granular)
    """

    _PRIORITY: dict[WorkspaceContextMode, int] = {
        WorkspaceContextMode.RETRIEVE_MCP: 0,
        WorkspaceContextMode.RETRIEVE_FS: 1,
        WorkspaceContextMode.PRIORITY_PATHS: 2,
        WorkspaceContextMode.INDEX_ONLY: 3,
        WorkspaceContextMode.FULL: 4,
    }

    @classmethod
    def is_valid_transition(
        cls,
        from_mode: WorkspaceContextMode,
        to_mode: WorkspaceContextMode,
    ) -> bool:
        """Return True only if the transition is an explicit downgrade of at most one level.

        Moving *up* (more context) is always allowed.
        Moving *down* by more than one level requires an explicit user action and
        is therefore rejected here (the caller must use retry_with or an explicit config).
        """
        from_prio = cls._PRIORITY.get(from_mode, 99)
        to_prio = cls._PRIORITY.get(to_mode, 99)
        # Going up (lower priority number = more context) is always valid
        if to_prio <= from_prio:
            return True
        # Downgrade: only one step at a time
        return (to_prio - from_prio) <= 1


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

class WorkspaceReaderPort(ABC):
    """Read-only workspace access — for use cases that never write."""

    @abstractmethod
    def list(
        self,
        path: str = "",
        *,
        max_depth: int = 3,
        max_files: int = 500,
    ) -> list[FileEntry]:
        """Return a list of files under *path* (relative to workspace root)."""

    @abstractmethod
    def read(self, path: str, *, max_chars: int = 50_000) -> ReadResult:
        """Read *path* (relative to workspace root), truncating at *max_chars*."""

    @abstractmethod
    def diff(
        self,
        path: str,
        from_ref: str,
        to_ref: str,
        *,
        max_chars: int = 20_000,
    ) -> str:
        """Return a git diff for *path* between *from_ref* and *to_ref*."""


class WorkspaceWriterPort(ABC):
    """Write-only workspace access — for use cases that apply patches."""

    @abstractmethod
    def write(self, path: str, content: str) -> None:
        """Write *content* to *path* (only when allow_write=True was passed at construction)."""


class WorkspaceIOPort(WorkspaceReaderPort, WorkspaceWriterPort):
    """Combined workspace I/O port (backward-compatible combined interface).

    Prefer WorkspaceReaderPort for read-only use cases (ISP).
    Prefer WorkspaceWriterPort for write-only use cases (ISP).
    """
