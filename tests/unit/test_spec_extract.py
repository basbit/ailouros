from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.application.extract_spec import (
    ExtractError,
    extract_spec_from_code,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
)


_SAMPLE_MODULE = '''
"""Hash passwords for the auth service."""

from __future__ import annotations


class PasswordHasher:
    def hash(self, plain: str) -> str:
        return plain

    def verify(self, plain: str, expected: str) -> bool:
        return self.hash(plain) == expected


def configure(cost: int = 12) -> dict[str, int]:
    return {"cost": cost}


def _private_helper() -> None:
    return None
'''


def _write_module(tmp_path: Path) -> Path:
    target = tmp_path / "src" / "auth" / "password.py"
    target.parent.mkdir(parents=True)
    target.write_text(_SAMPLE_MODULE, encoding="utf-8")
    return target


def test_extract_returns_document_with_public_surface(tmp_path: Path):
    code_path = _write_module(tmp_path)
    document = extract_spec_from_code(tmp_path, code_path)
    assert document.frontmatter.spec_id == "src/auth/password"
    body = document.body
    assert "PasswordHasher" in body
    assert "configure" in body
    assert "_private_helper" not in body


def test_extract_uses_module_docstring_for_purpose(tmp_path: Path):
    code_path = _write_module(tmp_path)
    document = extract_spec_from_code(tmp_path, code_path)
    assert "Hash passwords" in document.section("Purpose")


def test_extract_save_persists_spec(tmp_path: Path):
    code_path = _write_module(tmp_path)
    extract_spec_from_code(tmp_path, code_path, save=True)
    repository = FilesystemSpecRepository(tmp_path)
    assert repository.exists("src/auth/password")


def test_extract_rejects_non_python(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ExtractError):
        extract_spec_from_code(tmp_path, target)


def test_extract_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ExtractError):
        extract_spec_from_code(tmp_path, tmp_path / "nope.py")


def test_extract_rejects_path_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def x():\n    return 1\n", encoding="utf-8")
    try:
        with pytest.raises(ExtractError):
            extract_spec_from_code(tmp_path, outside)
    finally:
        outside.unlink(missing_ok=True)


def test_extract_rejects_reserved_spec_id(tmp_path: Path):
    code_path = _write_module(tmp_path)
    with pytest.raises(ExtractError):
        extract_spec_from_code(
            tmp_path, code_path, spec_id_override="_project",
        )


def test_extract_includes_codegen_target(tmp_path: Path):
    code_path = _write_module(tmp_path)
    document = extract_spec_from_code(tmp_path, code_path)
    assert document.frontmatter.codegen_targets == ("src/auth/password.py",)
