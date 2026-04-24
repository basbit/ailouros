"""Tests for FsSnapshotAdapter (fs_snapshot_adapter.py) — 0% coverage initially."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.App.workspace.infrastructure.fs_snapshot_adapter import FsSnapshotAdapter
from backend.App.workspace.domain.ports import FileEntry, ReadResult


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_construction_with_valid_dir():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp)
        assert adapter._root == Path(tmp).resolve()


def test_construction_with_path_object():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(Path(tmp))
        assert adapter._root.is_dir()


def test_construction_allow_write_false_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp)
        assert adapter._allow_write is False


def test_construction_allow_write_true():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp, allow_write=True)
        assert adapter._allow_write is True


# ---------------------------------------------------------------------------
# list()
# ---------------------------------------------------------------------------

def test_list_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp)
        entries = adapter.list()
        assert isinstance(entries, list)
        assert len(entries) == 0


def test_list_returns_file_entries():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "hello.txt").write_text("hello world")
        adapter = FsSnapshotAdapter(tmp)
        entries = adapter.list()
        assert len(entries) == 1
        assert isinstance(entries[0], FileEntry)
        assert entries[0].path == "hello.txt"
        assert entries[0].size_bytes > 0


def test_list_multiple_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.txt").write_text("aaa")
        (root / "b.txt").write_text("bbb")
        adapter = FsSnapshotAdapter(tmp)
        entries = adapter.list()
        paths = {e.path for e in entries}
        assert "a.txt" in paths
        assert "b.txt" in paths


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------

def test_read_returns_content():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "file.txt").write_text("hello content")
        adapter = FsSnapshotAdapter(tmp)
        result = adapter.read("file.txt")
        assert isinstance(result, ReadResult)
        assert result.content == "hello content"
        assert result.truncated is False


def test_read_truncates_when_over_max_chars():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "big.txt").write_text("x" * 200)
        adapter = FsSnapshotAdapter(tmp)
        result = adapter.read("big.txt", max_chars=50)
        assert len(result.content) == 50
        assert result.truncated is True


def test_read_reports_original_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        content = "hello"
        (root / "f.txt").write_text(content)
        adapter = FsSnapshotAdapter(tmp)
        result = adapter.read("f.txt")
        assert result.original_bytes == len(content.encode("utf-8"))


def test_read_raises_for_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp)
        with pytest.raises(FileNotFoundError):
            adapter.read("nonexistent.txt")


def test_read_raises_for_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp)
        with pytest.raises(ValueError, match="traversal"):
            adapter.read("../../etc/passwd")


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------

def test_write_denied_when_allow_write_false():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp, allow_write=False)
        with pytest.raises(PermissionError, match="allow_write=False"):
            adapter.write("out.txt", "content")


def test_write_allowed_when_allow_write_true():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp, allow_write=True)
        adapter.write("out.txt", "hello write")
        assert (Path(tmp) / "out.txt").read_text() == "hello write"


def test_write_creates_subdirs():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp, allow_write=True)
        adapter.write("sub/dir/file.txt", "deep write")
        assert (Path(tmp) / "sub" / "dir" / "file.txt").read_text() == "deep write"


def test_write_raises_for_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FsSnapshotAdapter(tmp, allow_write=True)
        with pytest.raises(ValueError, match="traversal"):
            adapter.write("../../etc/crontab", "evil")
