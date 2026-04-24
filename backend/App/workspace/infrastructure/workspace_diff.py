from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _run_git_command(
    workspace_root: Path,
    args: list[str],
    *,
    timeout: int,
    check_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode not in check_returncodes:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(stderr or f"git {' '.join(args)} failed")
    return result


def _git_repo_has_head(workspace_root: Path) -> bool:
    try:
        _run_git_command(
            workspace_root,
            ["rev-parse", "--verify", "HEAD"],
            timeout=5,
        )
        return True
    except RuntimeError:
        return False


def _tracked_diff_for_paths(workspace_root: Path, paths: list[str], *, has_head: bool) -> str:
    if not paths:
        return ""
    diff_args = ["diff", "--no-color"]
    if has_head:
        diff_args.append("HEAD")
    diff_args.extend(["--", *paths])
    result = _run_git_command(
        workspace_root,
        diff_args,
        timeout=30,
        check_returncodes=(0, 1),
    )
    return result.stdout


def _untracked_paths(workspace_root: Path, paths: list[str]) -> list[str]:
    if not paths:
        return []
    result = _run_git_command(
        workspace_root,
        ["ls-files", "--others", "--exclude-standard", "--", *paths],
        timeout=10,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _diff_for_untracked_file(workspace_root: Path, rel_path: str) -> str:
    result = _run_git_command(
        workspace_root,
        ["diff", "--no-index", "--no-color", "--", "/dev/null", rel_path],
        timeout=30,
        check_returncodes=(0, 1),
    )
    return result.stdout


def _parse_numstat(output: str) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        add_raw, remove_raw, _path = parts
        if add_raw.isdigit():
            added += int(add_raw)
        if remove_raw.isdigit():
            removed += int(remove_raw)
    return added, removed


def _count_file_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def capture_workspace_diff(workspace_root: Path, written_files: list[str]) -> dict[str, Any]:
    if not written_files:
        return {
            "diff_text": "",
            "files_changed": [],
            "stats": {"added": 0, "removed": 0, "files": 0},
            "source": "git",
        }

    try:
        files_changed = sorted(dict.fromkeys(path for path in written_files if path.strip()))
        _run_git_command(
            workspace_root,
            ["rev-parse", "--show-toplevel"],
            timeout=5,
        )
        has_head = _git_repo_has_head(workspace_root)
        untracked = set(_untracked_paths(workspace_root, files_changed))
        tracked = [path for path in files_changed if path not in untracked]

        tracked_diff = _tracked_diff_for_paths(workspace_root, tracked, has_head=has_head)
        tracked_numstat = ""
        if tracked:
            numstat_args = ["diff", "--numstat"]
            if has_head:
                numstat_args.append("HEAD")
            numstat_args.extend(["--", *tracked])
            tracked_numstat = _run_git_command(
                workspace_root,
                numstat_args,
                timeout=10,
                check_returncodes=(0, 1),
            ).stdout

        added, removed = _parse_numstat(tracked_numstat)
        untracked_diffs: list[str] = []
        for rel_path in sorted(untracked):
            untracked_diffs.append(_diff_for_untracked_file(workspace_root, rel_path))
            added += _count_file_lines(workspace_root / rel_path)

        diff_parts = [part for part in [tracked_diff, *untracked_diffs] if part]
        diff_text = "\n".join(part.rstrip("\n") for part in diff_parts)

        return {
            "diff_text": diff_text,
            "files_changed": files_changed,
            "stats": {"added": added, "removed": removed, "files": len(files_changed)},
            "source": "git",
        }

    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError) as exc:
        logger.debug("workspace_diff: git unavailable (%s), using file list", exc)
        return {
            "diff_text": "",
            "files_changed": written_files,
            "stats": {"added": 0, "removed": 0, "files": len(written_files)},
            "source": "file_list",
        }
