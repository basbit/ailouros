from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactStatus:
    path: str
    present: bool
    size: Optional[int] = None
    mtime: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_join(base: Path, rel: str) -> Optional[Path]:
    candidate = base / rel
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError):
        return None
    try:
        base_resolved = base.resolve(strict=False)
    except (OSError, ValueError):
        return None
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        return None
    return resolved


def check_scenario_artifacts(
    expected: Iterable[str],
    task_dir: Path,
) -> list[ArtifactStatus]:
    results: list[ArtifactStatus] = []
    for entry in expected:
        rel = (entry or "").strip()
        if not rel:
            continue
        full = _safe_join(task_dir, rel)
        if full is None:
            logger.warning(
                "scenario artifact path escapes task_dir, treated as missing: %r",
                entry,
            )
            results.append(ArtifactStatus(path=rel, present=False))
            continue
        if full.is_file():
            try:
                stat = full.stat()
            except OSError:
                results.append(ArtifactStatus(path=rel, present=False))
                continue
            results.append(
                ArtifactStatus(
                    path=rel,
                    present=True,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
        else:
            results.append(ArtifactStatus(path=rel, present=False))
    return results


def summarize_artifact_status(status: list[ArtifactStatus]) -> dict[str, int]:
    present = sum(1 for entry in status if entry.present)
    total = len(status)
    return {"present": present, "missing": total - present, "total": total}
