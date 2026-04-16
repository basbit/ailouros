"""Tests for wiki_tools (MCP tool layer for wiki search / read / write)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.App.integrations.infrastructure.mcp.wiki_tools import (
    handle_wiki_tool_call,
    wiki_tools_available,
    wiki_tools_definitions,
)

_WIKI_REL = Path(".swarm") / "wiki"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> str:
    """Create a minimal workspace with a .swarm/wiki/ directory."""
    wiki_root = tmp_path / _WIKI_REL
    wiki_root.mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


def _write_article(workspace_root: str, rel_path: str, body: str = "# Content\n\nSome text here.\n") -> Path:
    wiki_root = Path(workspace_root) / _WIKI_REL
    article = wiki_root / f"{rel_path}.md"
    article.parent.mkdir(parents=True, exist_ok=True)
    article.write_text(
        f"---\ntitle: {rel_path.split('/')[-1]}\ntags: []\nlinks: []\n---\n\n{body}",
        encoding="utf-8",
    )
    return article


# ---------------------------------------------------------------------------
# wiki_tools_available
# ---------------------------------------------------------------------------


def test_available_false_for_nonexistent_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_WIKI_TOOLS", "1")
    result = wiki_tools_available(str(tmp_path / "no_such_dir"))
    assert result is False


def test_available_false_when_env_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_WIKI_TOOLS", "0")
    workspace = _make_workspace(tmp_path)
    result = wiki_tools_available(workspace)
    assert result is False


def test_available_false_when_env_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARM_WIKI_TOOLS", raising=False)
    workspace = _make_workspace(tmp_path)
    result = wiki_tools_available(workspace)
    assert result is False


def test_available_true_when_enabled_and_dir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_WIKI_TOOLS", "1")
    workspace = _make_workspace(tmp_path)
    result = wiki_tools_available(workspace)
    assert result is True


def test_available_empty_string_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_WIKI_TOOLS", "1")
    result = wiki_tools_available("")
    assert result is False


# ---------------------------------------------------------------------------
# wiki_tools_definitions
# ---------------------------------------------------------------------------


def test_definitions_returns_three_tools() -> None:
    defs = wiki_tools_definitions()
    assert len(defs) == 3


def test_definitions_tool_names() -> None:
    names = {d["function"]["name"] for d in wiki_tools_definitions()}
    assert names == {"wiki_search", "wiki_read", "wiki_write"}


def test_definitions_are_openai_function_format() -> None:
    for defn in wiki_tools_definitions():
        assert defn["type"] == "function"
        assert "function" in defn
        func = defn["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func


# ---------------------------------------------------------------------------
# handle_wiki_tool_call — wiki_search with non-existent wiki root
# ---------------------------------------------------------------------------


def test_wiki_search_nonexistent_root_returns_error_string(tmp_path: Path) -> None:
    """A non-existent wiki root must return an error string, not raise."""
    workspace_root = str(tmp_path / "no_workspace_here")
    result = handle_wiki_tool_call("wiki_search", {"query": "anything"}, workspace_root)
    assert isinstance(result, str)
    assert "ERROR" in result
    # Must not be an exception traceback leaking out
    assert "Traceback" not in result


def test_wiki_read_nonexistent_root_returns_error_string(tmp_path: Path) -> None:
    workspace_root = str(tmp_path / "no_workspace_here")
    result = handle_wiki_tool_call("wiki_read", {"rel_path": "architecture/pipeline"}, workspace_root)
    assert isinstance(result, str)
    assert "ERROR" in result


def test_wiki_write_nonexistent_root_returns_error_string(tmp_path: Path) -> None:
    workspace_root = str(tmp_path / "no_workspace_here")
    result = handle_wiki_tool_call(
        "wiki_write",
        {"rel_path": "architecture/pipeline", "content": "# hi"},
        workspace_root,
    )
    assert isinstance(result, str)
    assert "ERROR" in result


# ---------------------------------------------------------------------------
# handle_wiki_tool_call — unknown tool name
# ---------------------------------------------------------------------------


def test_unknown_tool_name_returns_error(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call("wiki_explode", {}, workspace)
    assert "ERROR" in result
    assert "wiki_explode" in result


# ---------------------------------------------------------------------------
# handle_wiki_tool_call — wiki_write / wiki_read round-trip
# ---------------------------------------------------------------------------


def test_wiki_write_creates_article(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    content = "---\ntitle: Test\ntags: []\nlinks: []\n---\n\n# Test\n\nHello.\n"
    result = handle_wiki_tool_call(
        "wiki_write",
        {"rel_path": "features/test-article", "content": content},
        workspace,
    )
    assert "OK" in result or "wrote" in result.lower()
    article = Path(workspace) / _WIKI_REL / "features" / "test-article.md"
    assert article.is_file()
    assert article.read_text(encoding="utf-8") == content


def test_wiki_read_returns_content(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _write_article(workspace, "architecture/overview", body="Detail here.\n")
    result = handle_wiki_tool_call(
        "wiki_read",
        {"rel_path": "architecture/overview"},
        workspace,
    )
    assert "Detail here." in result


def test_wiki_read_nonexistent_article_returns_error(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call(
        "wiki_read",
        {"rel_path": "does/not/exist"},
        workspace,
    )
    assert "ERROR" in result


def test_wiki_write_missing_content_returns_error(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call(
        "wiki_write",
        {"rel_path": "features/empty", "content": "   "},
        workspace,
    )
    assert "ERROR" in result


def test_wiki_write_path_traversal_blocked(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call(
        "wiki_write",
        {"rel_path": "../../etc/passwd", "content": "pwned"},
        workspace,
    )
    assert "ERROR" in result


def test_wiki_read_path_traversal_blocked(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call(
        "wiki_read",
        {"rel_path": "../../../etc/passwd"},
        workspace,
    )
    assert "ERROR" in result


# ---------------------------------------------------------------------------
# handle_wiki_tool_call — wiki_search with empty wiki
# ---------------------------------------------------------------------------


def test_wiki_search_empty_wiki_returns_empty_json_array(tmp_path: Path) -> None:
    """wiki_search on an empty wiki returns an empty JSON array, not an error."""
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call("wiki_search", {"query": "authentication"}, workspace)
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert parsed == []


def test_wiki_search_missing_query_returns_error_json(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    result = handle_wiki_tool_call("wiki_search", {}, workspace)
    parsed = json.loads(result)
    assert "error" in parsed
