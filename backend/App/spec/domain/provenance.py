from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class KeepRegion:
    file_lines: tuple[int, int]
    reason: str
    added_at: str


@dataclass(frozen=True)
class ProvenanceSidecar:
    spec_id: str
    spec_version: int
    spec_hash: str
    generated_at: str
    model: str
    seed: int
    retry_count: int
    keep_regions: tuple[KeepRegion, ...]


class ProvenanceError(ValueError):
    pass


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ProvenanceError(f"provenance sidecar missing required field: {key!r}")
    return data[key]


def serialise_sidecar(sidecar: ProvenanceSidecar) -> str:
    payload: dict[str, Any] = {
        "spec_id": sidecar.spec_id,
        "spec_version": sidecar.spec_version,
        "spec_hash": sidecar.spec_hash,
        "generated_at": sidecar.generated_at,
        "model": sidecar.model,
        "seed": sidecar.seed,
        "retry_count": sidecar.retry_count,
        "keep_regions": [
            {
                "file_lines": list(region.file_lines),
                "reason": region.reason,
                "added_at": region.added_at,
            }
            for region in sidecar.keep_regions
        ],
    }
    return json.dumps(payload, indent=2)


def parse_sidecar(raw: str) -> ProvenanceSidecar:
    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProvenanceError(f"sidecar JSON is invalid: {exc}") from exc

    spec_id = str(_require(data, "spec_id"))
    spec_version = int(_require(data, "spec_version"))
    spec_hash = str(_require(data, "spec_hash"))
    generated_at = str(_require(data, "generated_at"))
    model = str(_require(data, "model"))
    seed = int(_require(data, "seed"))
    retry_count = int(_require(data, "retry_count"))

    raw_regions = _require(data, "keep_regions")
    if not isinstance(raw_regions, list):
        raise ProvenanceError("keep_regions must be a list")

    keep_regions: list[KeepRegion] = []
    for idx, item in enumerate(raw_regions):
        if not isinstance(item, dict):
            raise ProvenanceError(f"keep_regions[{idx}] must be an object")
        if "file_lines" not in item:
            raise ProvenanceError(f"keep_regions[{idx}] missing 'file_lines'")
        if "reason" not in item:
            raise ProvenanceError(f"keep_regions[{idx}] missing 'reason'")
        if "added_at" not in item:
            raise ProvenanceError(f"keep_regions[{idx}] missing 'added_at'")
        fl = item["file_lines"]
        if not (isinstance(fl, list) and len(fl) == 2):
            raise ProvenanceError(f"keep_regions[{idx}].file_lines must be [start, end]")
        keep_regions.append(
            KeepRegion(
                file_lines=(int(fl[0]), int(fl[1])),
                reason=str(item["reason"]),
                added_at=str(item["added_at"]),
            )
        )

    return ProvenanceSidecar(
        spec_id=spec_id,
        spec_version=spec_version,
        spec_hash=spec_hash,
        generated_at=generated_at,
        model=model,
        seed=seed,
        retry_count=retry_count,
        keep_regions=tuple(keep_regions),
    )


__all__ = [
    "KeepRegion",
    "ProvenanceSidecar",
    "ProvenanceError",
    "parse_sidecar",
    "serialise_sidecar",
]
