"""Tests for FsApiAdapter (C-3: WorkspaceIOPort infrastructure implementation)."""

from pathlib import Path

import pytest

from backend.App.workspace.infrastructure.fs_api_adapter import FsApiAdapter


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "src" / "utils.py").write_text("def util(): pass\n" * 50, encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
    return tmp_path


def test_list_returns_files(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    entries = adapter.list()
    paths = {e.path for e in entries}
    assert "src/main.py" in paths
    assert "README.md" in paths


def test_list_respects_max_files(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    entries = adapter.list(max_files=1)
    assert len(entries) == 1


def test_read_returns_content(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    result = adapter.read("src/main.py")
    assert "print" in result.content
    assert not result.truncated


def test_read_truncates_large_file(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    result = adapter.read("src/utils.py", max_chars=10)
    assert len(result.content) == 10
    assert result.truncated


def test_read_missing_file_raises(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    with pytest.raises(FileNotFoundError):
        adapter.read("nonexistent.py")


def test_path_traversal_raises(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    with pytest.raises(ValueError, match="traversal"):
        adapter.read("../secret.txt")


def test_path_traversal_in_list_raises(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    with pytest.raises(ValueError, match="traversal"):
        adapter.list("../../etc")


def test_write_denied_by_default(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace)
    with pytest.raises(PermissionError):
        adapter.write("new.txt", "content")


def test_write_allowed_when_enabled(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace, allow_write=True)
    adapter.write("output.txt", "hello")
    assert (workspace / "output.txt").read_text() == "hello"


def test_write_traversal_raises(workspace: Path) -> None:
    adapter = FsApiAdapter(workspace, allow_write=True)
    with pytest.raises(ValueError, match="traversal"):
        adapter.write("../evil.txt", "bad")
