from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from backend.App.spec.domain.ports import BlameLine, CommitEntry, GitHistoryPort

_PORCELAIN_HEADER_RE = re.compile(r"^([0-9a-f]{40})\s+\d+\s+(\d+)")


class GitUnavailableError(RuntimeError):
    pass


class GitFileUnknownError(RuntimeError):
    pass


class GitCommandError(RuntimeError):
    pass


def _require_git() -> str:
    path = shutil.which("git")
    if path is None:
        raise GitUnavailableError("git executable not found in PATH")
    return path


def _run(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError(f"git not found: {exc}") from exc
    if result.returncode != 0:
        raise GitCommandError(
            f"git command failed (exit {result.returncode}): "
            f"{' '.join(args)}\n{result.stderr.strip()}"
        )
    return result.stdout


def _assert_tracked(git_bin: str, cwd: Path, relative_path: str) -> None:
    try:
        result = subprocess.run(
            [git_bin, "ls-files", "--error-unmatch", relative_path],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError(f"git not found: {exc}") from exc
    if result.returncode != 0:
        raise GitFileUnknownError(
            f"file not tracked by git: {relative_path}"
        )


@lru_cache(maxsize=256)
def _cached_recent_commits(
    workspace_root: str,
    relative_path: str,
    limit: int,
) -> tuple[CommitEntry, ...]:
    git_bin = _require_git()
    cwd = Path(workspace_root)
    _assert_tracked(git_bin, cwd, relative_path)
    fmt = "%H%x1f%an%x1f%aI%x1f%s"
    raw = _run(
        [git_bin, "log", f"-n{limit}", f"--pretty=format:{fmt}", "--", relative_path],
        cwd,
    )
    entries: list[CommitEntry] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\x1f", 3)
        if len(parts) != 4:
            continue
        entries.append(CommitEntry(sha=parts[0], author=parts[1], date_iso=parts[2], subject=parts[3]))
    return tuple(entries)


@lru_cache(maxsize=256)
def _cached_blame_range(
    workspace_root: str,
    relative_path: str,
    start_line: int,
    end_line: int,
) -> tuple[BlameLine, ...]:
    git_bin = _require_git()
    cwd = Path(workspace_root)
    _assert_tracked(git_bin, cwd, relative_path)
    raw = _run(
        [
            git_bin,
            "blame",
            "--porcelain",
            f"-L{start_line},{end_line}",
            "--",
            relative_path,
        ],
        cwd,
    )
    from datetime import datetime, timezone

    lines: list[BlameLine] = []
    current_sha = ""
    current_author = ""
    current_date = ""
    current_lineno = 0
    for line in raw.splitlines():
        m = _PORCELAIN_HEADER_RE.match(line)
        if m:
            current_sha = m.group(1)
            current_lineno = int(m.group(2))
        elif line.startswith("author "):
            current_author = line[len("author "):]
        elif line.startswith("author-time "):
            ts = int(line[len("author-time "):])
            current_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        elif line.startswith("\t"):
            lines.append(
                BlameLine(
                    sha=current_sha,
                    author=current_author,
                    date_iso=current_date,
                    line_no=current_lineno,
                    line_text=line[1:],
                )
            )
    return tuple(lines)


def reset_git_history_cache() -> None:
    _cached_recent_commits.cache_clear()
    _cached_blame_range.cache_clear()


class SubprocessGitHistoryAdapter(GitHistoryPort):
    def recent_commits(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        limit: int = 10,
    ) -> tuple[CommitEntry, ...]:
        return _cached_recent_commits(str(workspace_root), str(relative_path), limit)

    def blame_range(
        self,
        workspace_root: str | Path,
        relative_path: str | Path,
        *,
        start_line: int,
        end_line: int,
    ) -> tuple[BlameLine, ...]:
        return _cached_blame_range(str(workspace_root), str(relative_path), start_line, end_line)


__all__ = [
    "GitCommandError",
    "GitFileUnknownError",
    "GitUnavailableError",
    "SubprocessGitHistoryAdapter",
    "reset_git_history_cache",
]
