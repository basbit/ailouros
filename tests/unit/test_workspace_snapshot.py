"""Tests for workspace_snapshot.py — collect_workspace_snapshot, build_input_with_workspace,
_truncate_snapshot_to_fit."""
from __future__ import annotations

import tempfile
from pathlib import Path

from backend.App.workspace.infrastructure.workspace_snapshot import (
    _truncate_snapshot_to_fit,
    build_input_with_workspace,
    collect_workspace_snapshot,
)


# ---------------------------------------------------------------------------
# _truncate_snapshot_to_fit
# ---------------------------------------------------------------------------

def test_truncate_no_truncation_needed():
    text = "short text"
    result = _truncate_snapshot_to_fit(text, 1000)
    assert result == text


def test_truncate_no_file_blocks_truncates_raw():
    text = "x" * 200
    result = _truncate_snapshot_to_fit(text, 100)
    assert result == text[:100]


def test_truncate_preserves_header_drops_file_blocks():
    header = "# Workspace root: /tmp/foo\n"
    file1 = "\n## file: a.py\n```\ncontent a\n```\n"
    file2 = "\n## file: b.py\n```\ncontent b\n```\n"
    snapshot = header + file1 + file2
    # Budget: header fits, file1 fits, file2 doesn't
    budget = len(header) + len(file1) + 1
    result = _truncate_snapshot_to_fit(snapshot, budget)
    assert header in result
    assert "a.py" in result
    assert "b.py" not in result or "truncated" in result


def test_truncate_adds_truncation_notice_when_files_dropped():
    header = "# Root\n"
    big_file = "\n## file: big.py\n```\n" + "x" * 100 + "\n```\n"
    snapshot = header + big_file
    budget = len(header) + 5  # too small for the file
    result = _truncate_snapshot_to_fit(snapshot, budget)
    assert "truncated" in result


# ---------------------------------------------------------------------------
# build_input_with_workspace
# ---------------------------------------------------------------------------

def test_build_input_no_snapshot_returns_task():
    result = build_input_with_workspace("do something", "")
    assert result == "do something"


def test_build_input_with_snapshot_contains_parts():
    result = build_input_with_workspace("do something", "file contents here")
    assert "do something" in result
    assert "file contents here" in result
    assert "# Workspace snapshot" in result


def test_build_input_with_manifest():
    result = build_input_with_workspace("task", "snapshot", manifest="project guide")
    assert "project guide" in result
    assert "# Project context" in result
    assert "task" in result


def test_build_input_custom_section_title():
    result = build_input_with_workspace("task", "snap", workspace_section_title="File index")
    assert "# File index" in result


def test_build_input_empty_title_falls_back():
    result = build_input_with_workspace("task", "snap", workspace_section_title="  ")
    assert "# Workspace snapshot" in result


def test_build_input_truncates_when_over_limit(monkeypatch):
    monkeypatch.setenv("SWARM_INPUT_MAX_CHARS", "200")
    # task + manifest both small; snapshot very large
    big_snapshot = "x" * 300
    result = build_input_with_workspace("task", big_snapshot)
    assert len(result) <= 400  # allow some slack for headers, but definitely not 300+400


def test_build_input_only_snapshot_no_manifest():
    result = build_input_with_workspace("task", "my snapshot content")
    # No project context section
    assert "Project context" not in result
    assert "my snapshot content" in result


# ---------------------------------------------------------------------------
# collect_workspace_snapshot
# ---------------------------------------------------------------------------

def test_collect_workspace_snapshot_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text, count = collect_workspace_snapshot(root, max_files=10)
        assert count == 0
        # Path may be resolved differently on macOS (/private/var vs /var)
        assert "Workspace root:" in text


def test_collect_workspace_snapshot_reads_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "hello.txt").write_text("hello world")
        text, count = collect_workspace_snapshot(root, max_files=10)
        assert count == 1
        assert "hello.txt" in text
        assert "hello world" in text


def test_collect_workspace_snapshot_respects_max_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in range(5):
            (root / f"file{i}.txt").write_text(f"content {i}")
        text, count = collect_workspace_snapshot(root, max_files=2)
        assert count == 2
        assert "truncated" in text


def test_collect_workspace_snapshot_skips_binary():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "binary.bin").write_bytes(b"\x00\x01\x02\x03binary data")
        text, count = collect_workspace_snapshot(root, max_files=10)
        assert count == 0
        assert "binary" in text.lower()


def test_collect_workspace_snapshot_skips_large_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "large.txt").write_text("x" * 200)
        # max_file_bytes=100 will skip the file
        text, count = collect_workspace_snapshot(root, max_files=10, max_file_bytes=100)
        assert count == 0
        assert "skipped" in text


def test_collect_workspace_snapshot_respects_max_total_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Two files that together exceed the byte limit
        (root / "a.txt").write_text("A" * 200)
        (root / "b.txt").write_text("B" * 200)
        text, count = collect_workspace_snapshot(
            root, max_files=10, max_total_bytes=250, max_file_bytes=10000
        )
        # Only one file should fit before byte limit hit
        assert "truncated" in text


def test_collect_workspace_snapshot_ignores_pycache_dir():
    """Verify __pycache__ is excluded (it's in _IGNORE_DIR_NAMES)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = root / "__pycache__"
        cache.mkdir()
        (cache / "mod.pyc").write_bytes(b"compiled")
        (root / "real.py").write_text("import os")
        text, count = collect_workspace_snapshot(root, max_files=10)
        assert "mod.pyc" not in text
        assert "real.py" in text


def test_collect_workspace_snapshot_ignores_node_modules():
    """Verify node_modules is excluded (it's in _IGNORE_DIR_NAMES)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nm = root / "node_modules"
        nm.mkdir()
        (nm / "package.json").write_text("{}")
        (root / "index.js").write_text("console.log('hi')")
        text, count = collect_workspace_snapshot(root, max_files=10)
        assert "package.json" not in text
        assert "index.js" in text
