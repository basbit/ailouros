from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.App.spec.domain.provenance import (
    KeepRegion,
    ProvenanceError,
    ProvenanceSidecar,
    parse_sidecar,
    serialise_sidecar,
)


def _minimal_sidecar() -> ProvenanceSidecar:
    return ProvenanceSidecar(
        spec_id="auth/password",
        spec_version=1,
        spec_hash="abc123",
        generated_at="2026-01-01T00:00:00Z",
        model="gpt-4",
        seed=42,
        retry_count=0,
        keep_regions=(),
    )


def test_serialise_round_trip_no_regions():
    sidecar = _minimal_sidecar()
    raw = serialise_sidecar(sidecar)
    parsed = parse_sidecar(raw)
    assert parsed == sidecar


def test_serialise_round_trip_with_regions():
    region = KeepRegion(
        file_lines=(10, 20),
        reason="custom-logic",
        added_at="2026-01-01T00:00:00Z",
    )
    sidecar = ProvenanceSidecar(
        spec_id="auth/password",
        spec_version=2,
        spec_hash="def456",
        generated_at="2026-02-01T00:00:00Z",
        model="claude-3",
        seed=99,
        retry_count=1,
        keep_regions=(region,),
    )
    parsed = parse_sidecar(serialise_sidecar(sidecar))
    assert parsed == sidecar


def test_parse_missing_spec_id_raises():
    raw = json.dumps({
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": [],
    })
    with pytest.raises(ProvenanceError, match="spec_id"):
        parse_sidecar(raw)


def test_parse_missing_spec_hash_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": [],
    })
    with pytest.raises(ProvenanceError, match="spec_hash"):
        parse_sidecar(raw)


def test_parse_missing_model_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": [],
    })
    with pytest.raises(ProvenanceError, match="model"):
        parse_sidecar(raw)


def test_parse_missing_keep_regions_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
    })
    with pytest.raises(ProvenanceError, match="keep_regions"):
        parse_sidecar(raw)


def test_parse_keep_regions_not_list_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": "bad",
    })
    with pytest.raises(ProvenanceError, match="list"):
        parse_sidecar(raw)


def test_parse_keep_region_missing_reason_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": [{"file_lines": [1, 5], "added_at": "2026-01-01T00:00:00Z"}],
    })
    with pytest.raises(ProvenanceError, match="reason"):
        parse_sidecar(raw)


def test_parse_keep_region_bad_file_lines_raises():
    raw = json.dumps({
        "spec_id": "x",
        "spec_version": 1,
        "spec_hash": "abc",
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "x",
        "seed": 0,
        "retry_count": 0,
        "keep_regions": [{"file_lines": [1], "reason": "x", "added_at": "2026-01-01T00:00:00Z"}],
    })
    with pytest.raises(ProvenanceError, match="file_lines"):
        parse_sidecar(raw)


def test_parse_invalid_json_raises():
    with pytest.raises(ProvenanceError, match="invalid"):
        parse_sidecar("{ not json }")


def test_serialise_produces_valid_json():
    raw = serialise_sidecar(_minimal_sidecar())
    data = json.loads(raw)
    assert data["spec_id"] == "auth/password"
    assert data["keep_regions"] == []


def test_serialise_region_file_lines_is_list():
    region = KeepRegion(file_lines=(5, 15), reason="r", added_at="2026-01-01T00:00:00Z")
    sidecar = _minimal_sidecar().__class__(
        spec_id="x",
        spec_version=1,
        spec_hash="h",
        generated_at="2026-01-01T00:00:00Z",
        model="m",
        seed=0,
        retry_count=0,
        keep_regions=(region,),
    )
    data = json.loads(serialise_sidecar(sidecar))
    assert data["keep_regions"][0]["file_lines"] == [5, 15]


def test_sidecar_fs_round_trip(tmp_path: Path):
    from backend.App.spec.infrastructure.sidecar_store import (
        read_sidecar,
        write_sidecar,
    )
    generated_file = tmp_path / "src" / "auth.py"
    generated_file.parent.mkdir()
    generated_file.write_text("# code", encoding="utf-8")
    sidecar = _minimal_sidecar()
    sidecar_path = write_sidecar(sidecar, generated_file, tmp_path)
    assert sidecar_path.is_file()
    recovered = read_sidecar(generated_file, tmp_path)
    assert recovered == sidecar


def test_sidecar_fs_missing_raises(tmp_path: Path):
    from backend.App.spec.infrastructure.sidecar_store import (
        SidecarNotFoundError,
        read_sidecar,
    )
    generated_file = tmp_path / "src" / "auth.py"
    generated_file.parent.mkdir()
    generated_file.write_text("x", encoding="utf-8")
    with pytest.raises(SidecarNotFoundError):
        read_sidecar(generated_file, tmp_path)


def test_sidecar_path_traversal_raises(tmp_path: Path):
    from backend.App.spec.infrastructure.sidecar_store import (
        SidecarStoreError,
        write_sidecar,
    )
    outside = tmp_path.parent / "evil.py"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(SidecarStoreError, match="outside workspace"):
        write_sidecar(_minimal_sidecar(), outside, tmp_path)
