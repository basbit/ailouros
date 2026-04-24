"""Stable filesystem anchors for the backend.

Single source of truth for project-relative paths (artifacts dir, app root, …).
Before this module existed, every call site did::

    Path(os.getenv("SWARM_ARTIFACTS_DIR", "var/artifacts")).resolve()

which resolves the default *relative to the current working directory of the
process*.  In production (systemd unit, Docker restart from a different
working dir) the CWD after a restart is not guaranteed to be the project root,
so new runs and retries of old runs would land in different directories and
``retry`` would fail with ``pipeline.json not found`` even though the file
exists at the original path.

Usage::

    from backend.App.paths import artifacts_root

    root = artifacts_root()          # <app>/var/artifacts by default
    pipeline = root / task_id / "pipeline.json"
"""

from __future__ import annotations

import os
from pathlib import Path


# ``backend/App/paths.py`` → parents[2] == ``app/`` regardless of CWD.
APP_ROOT: Path = Path(__file__).resolve().parents[2]

# Default, resolved at module import time.
_DEFAULT_ARTIFACTS_ROOT: Path = (APP_ROOT / "var" / "artifacts").resolve()


def artifacts_root() -> Path:
    """Return the configured artifacts root, anchored to the app root.

    Resolution order:
      1. ``SWARM_ARTIFACTS_DIR`` env var — absolute or relative path. Relative
         paths are resolved **against the app root**, not the process CWD, so a
         restart from a different CWD keeps reading/writing the same directory.
      2. Fallback: ``<app>/var/artifacts``.

    The returned :class:`~pathlib.Path` is always absolute and ``resolve()``-d.
    """
    raw = os.getenv("SWARM_ARTIFACTS_DIR", "").strip()
    if not raw:
        return _DEFAULT_ARTIFACTS_ROOT
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = APP_ROOT / candidate
    return candidate.resolve()


__all__ = ["APP_ROOT", "artifacts_root"]
