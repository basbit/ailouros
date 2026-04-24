"""Workspace context mode normalization — pure domain logic, no external deps (INV-7).

Rules:
- No imports from fastapi, redis, httpx, openai, anthropic, langgraph, or subprocess.
- Fallback is always logged explicitly by the caller (INV-1); this module just returns
  the canonical mode string.
"""

from __future__ import annotations

from backend.App.workspace.domain.ports import WorkspaceContextMode

# ---------------------------------------------------------------------------
# Alias table — maps user-supplied variant spellings to canonical values
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # retrieve_mcp variants
    "retrieve_mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrieve+mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrievemcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    "retrieve-mcp": WorkspaceContextMode.RETRIEVE_MCP.value,
    # retrieve_fs variants
    "retrieve_fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve+internal fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve+fs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrievefs": WorkspaceContextMode.RETRIEVE_FS.value,
    "retrieve-fs": WorkspaceContextMode.RETRIEVE_FS.value,
    # priority_paths variants
    "priority_paths": WorkspaceContextMode.PRIORITY_PATHS.value,
    "priority-paths": WorkspaceContextMode.PRIORITY_PATHS.value,
    "prioritypaths": WorkspaceContextMode.PRIORITY_PATHS.value,
    # index_only variants
    "index_only": WorkspaceContextMode.INDEX_ONLY.value,
    "index-only": WorkspaceContextMode.INDEX_ONLY.value,
    "indexonly": WorkspaceContextMode.INDEX_ONLY.value,
    # full variants
    "full": WorkspaceContextMode.FULL.value,
}

_DEFAULT_MODE: str = WorkspaceContextMode.FULL.value


def normalize_workspace_context_mode(raw: str) -> str:
    """Normalize raw context mode string to canonical WorkspaceContextMode value.

    Comparisons are case-insensitive and strip surrounding whitespace.
    Unknown values fall back to the default ("full") — the caller is responsible
    for logging this fallback (INV-1).

    Args:
        raw: User-supplied context mode string (may have alias spelling or wrong case).

    Returns:
        Canonical WorkspaceContextMode value string (e.g. "retrieve_mcp").
    """
    key = (raw or "").strip().lower()
    if not key:
        return _DEFAULT_MODE
    # Check aliases first (covers all canonical names too, since they are in the table)
    canonical = _ALIASES.get(key)
    if canonical is not None:
        return canonical
    # Try matching directly against enum values
    for mode in WorkspaceContextMode:
        if mode.value == key:
            return mode.value
    # Unknown — return default (caller must log; INV-1)
    return _DEFAULT_MODE
