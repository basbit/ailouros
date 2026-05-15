from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.App.spec.infrastructure.sidecar_store import (
    SidecarStoreError,
    read_sidecar,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
    SpecRepositoryError,
)

_DEFAULT_KEEP_REGION_AGE_DAYS = 90
_ENV_KEEP_REGION_AGE_DAYS = "SWARM_KEEP_REGION_AGE_DAYS"


@dataclass(frozen=True)
class StaleEntry:
    spec_id: str
    target_path: str
    spec_hash: str
    sidecar_hash: str


@dataclass(frozen=True)
class AgedKeepRegion:
    spec_id: str
    target_path: str
    reason: str
    added_at: str
    age_days: int


@dataclass(frozen=True)
class DriftReport:
    stale_code: tuple[StaleEntry, ...]
    stale_specs: tuple[StaleEntry, ...]
    aged_keep_regions: tuple[AgedKeepRegion, ...]


def _keep_region_age_days() -> int:
    raw = os.environ.get(_ENV_KEEP_REGION_AGE_DAYS, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError as exc:
            raise SpecRepositoryError(
                f"{_ENV_KEEP_REGION_AGE_DAYS}={raw!r} is not a valid integer"
            ) from exc
        return value
    return _DEFAULT_KEEP_REGION_AGE_DAYS


def _days_since(iso_timestamp: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(tz=timezone.utc)
    delta = now - dt.astimezone(timezone.utc)
    return delta.days


def detect_drift(workspace_root: str | Path) -> DriftReport:
    repository = FilesystemSpecRepository(workspace_root)
    ws_path = repository._workspace_root
    age_threshold = _keep_region_age_days()

    stale_code: list[StaleEntry] = []
    stale_specs: list[StaleEntry] = []
    aged_regions: list[AgedKeepRegion] = []

    for spec_id in repository.iter_spec_ids():
        try:
            document = repository.load(spec_id)
        except SpecRepositoryError:
            continue

        if not document.frontmatter.codegen_targets:
            continue

        current_hash = document.codegen_hash()

        for target_rel in document.frontmatter.codegen_targets:
            target_path = ws_path / target_rel

            try:
                sidecar = read_sidecar(target_path, ws_path)
            except SidecarStoreError:
                continue

            if sidecar.spec_hash != current_hash:
                stale_code.append(
                    StaleEntry(
                        spec_id=spec_id,
                        target_path=target_rel,
                        spec_hash=current_hash,
                        sidecar_hash=sidecar.spec_hash,
                    )
                )

            if sidecar.spec_id == spec_id and sidecar.spec_hash != current_hash:
                stale_specs.append(
                    StaleEntry(
                        spec_id=spec_id,
                        target_path=target_rel,
                        spec_hash=current_hash,
                        sidecar_hash=sidecar.spec_hash,
                    )
                )

            for region in sidecar.keep_regions:
                days = _days_since(region.added_at)
                if days is not None and days >= age_threshold:
                    aged_regions.append(
                        AgedKeepRegion(
                            spec_id=spec_id,
                            target_path=target_rel,
                            reason=region.reason,
                            added_at=region.added_at,
                            age_days=days,
                        )
                    )

    return DriftReport(
        stale_code=tuple(stale_code),
        stale_specs=tuple(stale_specs),
        aged_keep_regions=tuple(aged_regions),
    )


__all__ = [
    "AgedKeepRegion",
    "DriftReport",
    "StaleEntry",
    "detect_drift",
]
