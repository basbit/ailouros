"""Tests for backend.App.workspace.domain.context_mode (H-2)."""

from __future__ import annotations

import pytest

from backend.App.workspace.domain.context_mode import normalize_workspace_context_mode
from backend.App.workspace.domain.ports import WorkspaceContextMode


# ---------------------------------------------------------------------------
# Canonical mode values pass through unchanged
# ---------------------------------------------------------------------------

def test_canonical_retrieve_mcp() -> None:
    assert normalize_workspace_context_mode("retrieve_mcp") == "retrieve_mcp"


def test_canonical_retrieve_fs() -> None:
    assert normalize_workspace_context_mode("retrieve_fs") == "retrieve_fs"


def test_canonical_priority_paths() -> None:
    assert normalize_workspace_context_mode("priority_paths") == "priority_paths"


def test_canonical_index_only() -> None:
    assert normalize_workspace_context_mode("index_only") == "index_only"


def test_canonical_full() -> None:
    assert normalize_workspace_context_mode("full") == "full"


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------

def test_alias_retrieve_plus_mcp() -> None:
    assert normalize_workspace_context_mode("retrieve+mcp") == "retrieve_mcp"


def test_alias_retrieve_plus_internal_fs() -> None:
    assert normalize_workspace_context_mode("retrieve+internal fs") == "retrieve_fs"


def test_alias_retrieve_plus_fs() -> None:
    assert normalize_workspace_context_mode("retrieve+fs") == "retrieve_fs"


def test_alias_case_insensitive() -> None:
    assert normalize_workspace_context_mode("RETRIEVE_MCP") == "retrieve_mcp"
    assert normalize_workspace_context_mode("Index_Only") == "index_only"
    assert normalize_workspace_context_mode("FULL") == "full"


def test_alias_with_leading_trailing_spaces() -> None:
    assert normalize_workspace_context_mode("  retrieve_mcp  ") == "retrieve_mcp"


# ---------------------------------------------------------------------------
# Unknown and empty values
# ---------------------------------------------------------------------------

def test_empty_string_returns_default() -> None:
    result = normalize_workspace_context_mode("")
    assert result == WorkspaceContextMode.FULL.value


def test_whitespace_only_returns_default() -> None:
    result = normalize_workspace_context_mode("   ")
    assert result == WorkspaceContextMode.FULL.value


def test_unknown_value_returns_default() -> None:
    result = normalize_workspace_context_mode("totally_unknown_mode")
    assert result == WorkspaceContextMode.FULL.value


def test_none_like_empty_returns_default() -> None:
    # Simulates caller passing empty string from env var
    result = normalize_workspace_context_mode("")
    assert result == "full"


# ---------------------------------------------------------------------------
# Return values are valid WorkspaceContextMode members
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("retrieve_mcp", WorkspaceContextMode.RETRIEVE_MCP),
    ("retrieve_fs", WorkspaceContextMode.RETRIEVE_FS),
    ("priority_paths", WorkspaceContextMode.PRIORITY_PATHS),
    ("index_only", WorkspaceContextMode.INDEX_ONLY),
    ("full", WorkspaceContextMode.FULL),
])
def test_result_is_valid_enum_value(raw: str, expected: WorkspaceContextMode) -> None:
    result = normalize_workspace_context_mode(raw)
    assert result == expected.value
    # Verify it can round-trip into the enum
    assert WorkspaceContextMode(result) == expected
