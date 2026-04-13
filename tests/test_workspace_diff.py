from __future__ import annotations

import subprocess
from pathlib import Path

from backend.App.workspace.infrastructure.workspace_diff import capture_workspace_diff


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_capture_workspace_diff_limits_output_to_changed_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "other.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt", "other.txt")
    _git(repo, "commit", "-m", "initial")

    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "new.txt").write_text("new file\n", encoding="utf-8")
    (repo / "other.txt").write_text("unrelated change\n", encoding="utf-8")

    result = capture_workspace_diff(repo, ["tracked.txt", "new.txt"])

    assert result["files_changed"] == ["new.txt", "tracked.txt"]
    assert "tracked.txt" in result["diff_text"]
    assert "new.txt" in result["diff_text"]
    assert "other.txt" not in result["diff_text"]
    assert result["stats"] == {"added": 2, "removed": 1, "files": 2}


def test_capture_workspace_diff_falls_back_to_file_list_outside_git(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "hello.txt").write_text("hello\n", encoding="utf-8")

    result = capture_workspace_diff(workspace_root, ["hello.txt"])

    assert result["source"] == "file_list"
    assert result["files_changed"] == ["hello.txt"]
    assert result["diff_text"] == ""
