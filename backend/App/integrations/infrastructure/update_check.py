from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UpdateStatus:
    checked: bool
    unknown: bool
    behind: int
    ahead: int
    current_ref: str
    remote_ref: str
    branch: str
    reason: str = ""

    def update_available(self) -> bool:
        return self.checked and not self.unknown and self.behind > 0


_UNKNOWN = UpdateStatus(
    checked=False, unknown=True, behind=0, ahead=0,
    current_ref="", remote_ref="", branch="", reason="not checked yet",
)

_status_lock = threading.Lock()
_status: UpdateStatus = _UNKNOWN


def _skip_check() -> bool:
    return os.getenv("SWARM_SKIP_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}


def _repo_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    return None


def _git(args: list[str], cwd: Path, timeout: float = 10.0) -> tuple[int, str]:
    git = shutil.which("git")
    if not git:
        return 127, "git binary not found on PATH"
    try:
        out = subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return out.returncode, (out.stdout or out.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} timed out after {timeout}s"
    except OSError as exc:
        return 1, f"git {args[0]} failed: {exc}"


def check_for_updates(*, fetch: bool = True) -> UpdateStatus:
    global _status
    if _skip_check():
        _status = UpdateStatus(
            checked=True, unknown=True,
            behind=0, ahead=0, current_ref="", remote_ref="", branch="",
            reason="SWARM_SKIP_UPDATE_CHECK=1",
        )
        return _status

    root = _repo_root()
    if root is None:
        _status = UpdateStatus(
            checked=True, unknown=True,
            behind=0, ahead=0, current_ref="", remote_ref="", branch="",
            reason="no .git directory found",
        )
        return _status

    rc, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    if rc != 0 or not branch or branch == "HEAD":
        _status = UpdateStatus(
            checked=True, unknown=True,
            behind=0, ahead=0, current_ref="", remote_ref="", branch="",
            reason=f"detached HEAD or git rev-parse failed: {branch[:120]}",
        )
        return _status

    if fetch:
        rc_f, out_f = _git(["fetch", "--quiet", "--no-tags"], cwd=root, timeout=20.0)
        if rc_f != 0:
            _status = UpdateStatus(
                checked=True, unknown=True,
                behind=0, ahead=0, current_ref="", remote_ref="", branch=branch,
                reason=f"git fetch failed: {out_f[:120]}",
            )
            return _status

    remote_ref = f"origin/{branch}"
    rc1, head_sha = _git(["rev-parse", "--short", "HEAD"], cwd=root)
    rc2, remote_sha = _git(["rev-parse", "--short", remote_ref], cwd=root)
    rc3, counts = _git(["rev-list", "--left-right", "--count", f"HEAD...{remote_ref}"], cwd=root)
    if rc1 != 0 or rc2 != 0 or rc3 != 0:
        _status = UpdateStatus(
            checked=True, unknown=True,
            behind=0, ahead=0, current_ref="", remote_ref="", branch=branch,
            reason=f"no remote tracking branch {remote_ref}",
        )
        return _status
    try:
        ahead_s, behind_s = counts.split()
        ahead_n, behind_n = int(ahead_s), int(behind_s)
    except (ValueError, AttributeError):
        _status = UpdateStatus(
            checked=True, unknown=True,
            behind=0, ahead=0, current_ref=head_sha, remote_ref=remote_sha, branch=branch,
            reason=f"could not parse rev-list output: {counts!r}",
        )
        return _status

    _status = UpdateStatus(
        checked=True, unknown=False,
        behind=behind_n, ahead=ahead_n,
        current_ref=head_sha, remote_ref=remote_sha, branch=branch,
    )
    logger.info(
        "update_check: branch=%s behind=%d ahead=%d head=%s remote=%s",
        branch, behind_n, ahead_n, head_sha, remote_sha,
    )
    return _status


def run_update_check_in_background() -> None:
    def _worker() -> None:
        try:
            check_for_updates(fetch=True)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("update_check background worker raised: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="swarm-update-check").start()


def get_status() -> UpdateStatus:
    with _status_lock:
        return _status


def status_as_dict() -> dict:
    return asdict(get_status())
