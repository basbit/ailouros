from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
    SpecAlreadyExistsError,
    SpecNotFoundError,
    SpecRepositoryError,
)


def _document(spec_id: str = "auth/password") -> SpecDocument:
    body = (
        "\n## Purpose\n\nHash passwords safely.\n\n"
        "## Public Contract\n\n```python {dsl=python-sig}\n"
        "def hash_password(plain: str) -> str: ...\n```\n"
    )
    frontmatter = SpecFrontmatter(
        spec_id=spec_id,
        version=1,
        status="draft",
        privacy="internal",
        codegen_targets=("src/auth/password.py",),
    )
    return SpecDocument(frontmatter=frontmatter, body=body, sections=())


def test_save_and_load_roundtrip(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    document = _document()
    path = repo.save(document)
    assert path.is_file()
    loaded = repo.load("auth/password")
    assert loaded.frontmatter.spec_id == "auth/password"
    assert "Hash passwords" in loaded.section("Purpose")


def test_load_missing_raises(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    with pytest.raises(SpecNotFoundError):
        repo.load("does/not/exist")


def test_save_without_overwrite_raises(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    document = _document()
    repo.save(document)
    with pytest.raises(SpecAlreadyExistsError):
        repo.save(document, overwrite=False)


def test_list_specs_returns_sorted_ids(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    repo.save(_document("z/last"))
    repo.save(_document("a/first"))
    repo.save(_document("m/middle"))
    assert repo.list_specs() == ["a/first", "m/middle", "z/last"]


def test_directory_traversal_is_blocked(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    with pytest.raises(SpecRepositoryError):
        repo.load("../../etc/passwd")
    with pytest.raises(SpecRepositoryError):
        repo.load("nested/../../escape")


def test_empty_spec_id_rejected(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    with pytest.raises(SpecRepositoryError):
        repo.load("")


def test_nonexistent_workspace_root_rejected(tmp_path: Path):
    missing = tmp_path / "nope"
    with pytest.raises(SpecRepositoryError):
        FilesystemSpecRepository(missing)


def test_ensure_initialised_creates_dir(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    created = repo.ensure_initialised()
    assert created.is_dir()
    assert created.name == "specs"


def test_delete_removes_spec(tmp_path: Path):
    repo = FilesystemSpecRepository(tmp_path)
    repo.save(_document())
    repo.delete("auth/password")
    assert not repo.exists("auth/password")
