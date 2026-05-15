
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class WorkspaceContextMode(str, Enum):
    RETRIEVE_MCP = "retrieve_mcp"
    RETRIEVE_FS = "retrieve_fs"
    PRIORITY_PATHS = "priority_paths"
    INDEX_ONLY = "index_only"
    FULL = "full"


WORKSPACE_CONTEXT_MODE_DEFAULT: str = "retrieve"
WORKSPACE_CONTEXT_MODE_TOOLS_ONLY: str = "tools_only"


@dataclass(frozen=True)
class FileEntry:
    path: str
    size_bytes: int


@dataclass(frozen=True)
class ReadResult:
    content: str
    truncated: bool
    original_bytes: int


class WorkspaceContextPolicy:

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
        from_prio = cls._PRIORITY.get(from_mode, 99)
        to_prio = cls._PRIORITY.get(to_mode, 99)
        if to_prio <= from_prio:
            return True
        return (to_prio - from_prio) <= 1


class WorkspaceReaderPort(ABC):

    @abstractmethod
    def list(
        self,
        path: str = "",
        *,
        max_depth: int = 3,
        max_files: int = 500,
    ) -> list[FileEntry]:
        pass

    @abstractmethod
    def read(self, path: str, *, max_chars: int = 50_000) -> ReadResult:
        pass

    @abstractmethod
    def diff(
        self,
        path: str,
        from_ref: str,
        to_ref: str,
        *,
        max_chars: int = 20_000,
    ) -> str:
        pass


class WorkspaceWriterPort(ABC):

    @abstractmethod
    def write(self, path: str, content: str) -> None:
        pass


class WorkspaceIOPort(WorkspaceReaderPort, WorkspaceWriterPort):
    pass
