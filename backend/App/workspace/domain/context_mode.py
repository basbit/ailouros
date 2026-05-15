
from __future__ import annotations

from backend.App.workspace.domain.ports import WorkspaceContextMode

_ALIASES: dict[str, str] = {
    "retrieve_mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrieve+mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrievemcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrieve-mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrieve_fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve+internal fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve+fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrievefs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve-fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "priority_paths": WorkspaceContextMode.PRIORITY_PATHS.value,
    "priority-paths": WorkspaceContextMode.PRIORITY_PATHS.value,
    "prioritypaths": WorkspaceContextMode.PRIORITY_PATHS.value,
    "index_only": WorkspaceContextMode.INDEX_ONLY.value,
    "index-only": WorkspaceContextMode.INDEX_ONLY.value,
    "indexonly": WorkspaceContextMode.INDEX_ONLY.value,
    "full": WorkspaceContextMode.FULL.value,
}

_DEFAULT_MODE: str = WorkspaceContextMode.FULL.value


def normalize_workspace_context_mode(raw: str) -> str:
    key = (raw or "").strip().lower()
    if not key:
        return _DEFAULT_MODE
    canonical = _ALIASES.get(key)
    if canonical is not None:
        return canonical
    for mode in WorkspaceContextMode:
        if mode.value == key:
            return mode.value
    return _DEFAULT_MODE
