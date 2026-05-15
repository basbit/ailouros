from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.App.spec.application.drift_detector import (
    DriftReport,
    detect_drift,
)
from backend.App.spec.domain.provenance import ProvenanceSidecar
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
    render_spec,
)
from backend.App.spec.infrastructure.sidecar_store import write_sidecar

_BODY = (
    "\n## Purpose\n\nTest.\n\n"
    "## Public Contract\n\ndef foo() -> None: ...\n\n"
    "## Behaviour\n\nDoes nothing.\n\n"
)


def _write_spec(
    workspace_root: Path,
    spec_id: str,
    target: str = "src/foo.py",
    version: int = 1,
) -> SpecDocument:
    parts = spec_id.split("/")
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=version,
        status="draft",
        privacy="internal",
        codegen_targets=(target,),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_BODY, sections=())
    spec_dir = workspace_root / ".swarm" / "specs" / "/".join(parts[:-1])
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / (parts[-1] + ".md")).write_text(render_spec(document), encoding="utf-8")
    return document


def _reload_document(workspace_root: Path, spec_id: str) -> SpecDocument:
    from backend.App.spec.infrastructure.spec_repository_fs import FilesystemSpecRepository
    return FilesystemSpecRepository(workspace_root).load(spec_id)


def _write_sidecar_for(
    workspace_root: Path,
    document: SpecDocument,
    target_rel: str,
    *,
    hash_override: str | None = None,
    added_at: str | None = None,
) -> None:
    target_path = workspace_root / target_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("# code", encoding="utf-8")

    from backend.App.spec.domain.provenance import KeepRegion

    keep: list[KeepRegion] = []
    if added_at:
        keep.append(KeepRegion(file_lines=(1, 5), reason="custom", added_at=added_at))

    reloaded = _reload_document(workspace_root, document.frontmatter.spec_id)
    spec_hash = hash_override if hash_override else reloaded.codegen_hash()
    sidecar = ProvenanceSidecar(
        spec_id=document.frontmatter.spec_id,
        spec_version=document.frontmatter.version,
        spec_hash=spec_hash,
        generated_at="2026-01-01T00:00:00Z",
        model="stub",
        seed=0,
        retry_count=0,
        keep_regions=tuple(keep),
    )
    write_sidecar(sidecar, target_path, workspace_root)


def test_empty_workspace_returns_empty_report(tmp_path: Path):
    (tmp_path / ".swarm" / "specs").mkdir(parents=True)
    report = detect_drift(tmp_path)
    assert report.stale_code == ()
    assert report.stale_specs == ()
    assert report.aged_keep_regions == ()


def test_no_specs_returns_empty_report(tmp_path: Path):
    report = detect_drift(tmp_path)
    assert isinstance(report, DriftReport)


def test_spec_with_no_sidecar_not_reported_as_stale(tmp_path: Path):
    _write_spec(tmp_path, "auth/login")
    report = detect_drift(tmp_path)
    assert report.stale_code == ()


def test_matching_hashes_no_stale_code(tmp_path: Path):
    doc = _write_spec(tmp_path, "auth/login", target="src/login.py")
    _write_sidecar_for(tmp_path, doc, "src/login.py")
    report = detect_drift(tmp_path)
    assert report.stale_code == ()


def test_changed_spec_detected_as_stale_code(tmp_path: Path):
    doc = _write_spec(tmp_path, "auth/login", target="src/login.py")
    _write_sidecar_for(tmp_path, doc, "src/login.py", hash_override="old_hash_abc")
    report = detect_drift(tmp_path)
    assert len(report.stale_code) == 1
    assert report.stale_code[0].spec_id == "auth/login"
    assert report.stale_code[0].sidecar_hash == "old_hash_abc"


def test_aged_keep_region_detected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_KEEP_REGION_AGE_DAYS", "30")
    old_date = (datetime.now(tz=timezone.utc) - timedelta(days=45)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    doc = _write_spec(tmp_path, "auth/login", target="src/login.py")
    _write_sidecar_for(tmp_path, doc, "src/login.py", added_at=old_date)
    report = detect_drift(tmp_path)
    assert len(report.aged_keep_regions) == 1
    assert report.aged_keep_regions[0].age_days >= 45


def test_fresh_keep_region_not_detected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_KEEP_REGION_AGE_DAYS", "90")
    recent = (datetime.now(tz=timezone.utc) - timedelta(days=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    doc = _write_spec(tmp_path, "auth/login", target="src/login.py")
    _write_sidecar_for(tmp_path, doc, "src/login.py", added_at=recent)
    report = detect_drift(tmp_path)
    assert report.aged_keep_regions == ()


def test_invalid_age_env_var_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWARM_KEEP_REGION_AGE_DAYS", "not-a-number")
    _write_spec(tmp_path, "auth/login", target="src/login.py")
    from backend.App.spec.infrastructure.spec_repository_fs import SpecRepositoryError
    with pytest.raises(SpecRepositoryError, match="not a valid integer"):
        detect_drift(tmp_path)


def test_spec_without_codegen_targets_skipped(tmp_path: Path):
    frontmatter = SpecFrontmatter(
        spec_id="util/helper",
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=(),
    )
    document = SpecDocument(frontmatter=frontmatter, body=_BODY, sections=())
    spec_dir = tmp_path / ".swarm" / "specs" / "util"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "helper.md").write_text(render_spec(document), encoding="utf-8")
    report = detect_drift(tmp_path)
    assert report.stale_code == ()


def test_multiple_stale_entries(tmp_path: Path):
    for name in ("alpha", "beta"):
        doc = _write_spec(tmp_path, f"mod/{name}", target=f"src/{name}.py")
        _write_sidecar_for(tmp_path, doc, f"src/{name}.py", hash_override="stale")
    report = detect_drift(tmp_path)
    assert len(report.stale_code) == 2
