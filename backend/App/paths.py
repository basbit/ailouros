
from __future__ import annotations

import os
from pathlib import Path

APP_ROOT: Path = Path(__file__).resolve().parents[2]

_DEFAULT_ARTIFACTS_ROOT: Path = (APP_ROOT / "var" / "artifacts").resolve()


def artifacts_root() -> Path:
    raw = os.getenv("SWARM_ARTIFACTS_DIR", "").strip()
    if not raw:
        return _DEFAULT_ARTIFACTS_ROOT
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = APP_ROOT / candidate
    return candidate.resolve()


__all__ = ["APP_ROOT", "artifacts_root"]
