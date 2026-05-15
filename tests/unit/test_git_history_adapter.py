from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.App.spec.infrastructure.git_history_adapter import (
    GitFileUnknownError,
    SubprocessGitHistoryAdapter,
    reset_git_history_cache,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git not installed",
)

_DUMMY_AUTHOR = "Test Author"
_DUMMY_EMAIL = "test@example.com"


def _init_repo(tmp_path: Path) -> Path:
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)}
    for cmd in [
        ["git", "init", str(tmp_path)],
        ["git", "-C", str(tmp_path), "config", "user.name", _DUMMY_AUTHOR],
        ["git", "-C", str(tmp_path), "config", "user.email", _DUMMY_EMAIL],
    ]:
        subprocess.run(cmd, check=True, capture_output=True, env={**__import__("os").environ, **env})
    return tmp_path


def _commit_file(repo: Path, rel: str, content: str, message: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", rel], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": _DUMMY_AUTHOR,
            "GIT_AUTHOR_EMAIL": _DUMMY_EMAIL,
            "GIT_COMMITTER_NAME": _DUMMY_AUTHOR,
            "GIT_COMMITTER_EMAIL": _DUMMY_EMAIL,
        },
    )


@pytest.fixture(autouse=True)
def clear_cache():
    reset_git_history_cache()
    yield
    reset_git_history_cache()


def test_recent_commits_single_commit(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/hello.py", "x = 1\n", "initial commit")

    adapter = SubprocessGitHistoryAdapter()
    commits = adapter.recent_commits(repo, "src/hello.py", limit=10)

    assert len(commits) == 1
    assert commits[0].subject == "initial commit"
    assert commits[0].author == _DUMMY_AUTHOR
    assert len(commits[0].sha) == 40
    assert "T" in commits[0].date_iso or "-" in commits[0].date_iso


def test_recent_commits_multiple_ordered(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "src/mod.py", "a = 1\n", "first")
    _commit_file(repo, "src/mod.py", "a = 2\n", "second")
    _commit_file(repo, "src/mod.py", "a = 3\n", "third")

    adapter = SubprocessGitHistoryAdapter()
    commits = adapter.recent_commits(repo, "src/mod.py", limit=10)

    assert len(commits) == 3
    assert commits[0].subject == "third"
    assert commits[1].subject == "second"
    assert commits[2].subject == "first"


def test_recent_commits_limit_respected(tmp_path: Path):
    repo = _init_repo(tmp_path)
    for i in range(5):
        _commit_file(repo, "f.py", f"x = {i}\n", f"commit {i}")

    adapter = SubprocessGitHistoryAdapter()
    commits = adapter.recent_commits(repo, "f.py", limit=3)

    assert len(commits) == 3


def test_blame_range_basic(tmp_path: Path):
    repo = _init_repo(tmp_path)
    content = "line one\nline two\nline three\nline four\n"
    _commit_file(repo, "src/code.py", content, "add code")

    adapter = SubprocessGitHistoryAdapter()
    blame = adapter.blame_range(repo, "src/code.py", start_line=1, end_line=3)

    assert len(blame) == 3
    assert blame[0].line_no == 1
    assert blame[0].line_text == "line one"
    assert blame[1].line_no == 2
    assert blame[1].line_text == "line two"
    assert blame[2].line_no == 3
    assert blame[2].line_text == "line three"


def test_blame_single_line(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "g.py", "alpha\nbeta\ngamma\n", "msg")

    adapter = SubprocessGitHistoryAdapter()
    blame = adapter.blame_range(repo, "g.py", start_line=2, end_line=2)

    assert len(blame) == 1
    assert blame[0].line_no == 2
    assert blame[0].line_text == "beta"


def test_blame_author_populated(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "h.py", "x = 1\n", "msg")

    adapter = SubprocessGitHistoryAdapter()
    blame = adapter.blame_range(repo, "h.py", start_line=1, end_line=1)

    assert blame[0].author == _DUMMY_AUTHOR
    assert len(blame[0].sha) == 40


def test_untracked_file_raises_git_file_unknown(tmp_path: Path):
    repo = _init_repo(tmp_path)
    untracked = repo / "not_tracked.py"
    untracked.write_text("x = 1\n")

    adapter = SubprocessGitHistoryAdapter()
    with pytest.raises(GitFileUnknownError):
        adapter.recent_commits(repo, "not_tracked.py")


def test_blame_untracked_raises(tmp_path: Path):
    repo = _init_repo(tmp_path)
    (repo / "ghost.py").write_text("a\n")

    adapter = SubprocessGitHistoryAdapter()
    with pytest.raises(GitFileUnknownError):
        adapter.blame_range(repo, "ghost.py", start_line=1, end_line=1)


def test_lru_cache_returns_same_object(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "cached.py", "y = 2\n", "cached commit")

    adapter = SubprocessGitHistoryAdapter()
    result_a = adapter.recent_commits(repo, "cached.py", limit=5)
    result_b = adapter.recent_commits(repo, "cached.py", limit=5)

    assert result_a is result_b


def test_reset_cache_allows_fresh_read(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _commit_file(repo, "evolve.py", "v = 1\n", "v1")

    adapter = SubprocessGitHistoryAdapter()
    before = adapter.recent_commits(repo, "evolve.py", limit=10)

    reset_git_history_cache()
    _commit_file(repo, "evolve.py", "v = 2\n", "v2")

    after = adapter.recent_commits(repo, "evolve.py", limit=10)
    assert len(after) == len(before) + 1
