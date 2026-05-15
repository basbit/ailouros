from __future__ import annotations

from pathlib import Path

import pytest

from backend.App.spec.application.use_cases import (
    init_workspace_specs,
    list_specs,
    show_spec,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    SpecNotFoundError,
)


def test_init_creates_project_and_schema(tmp_path: Path):
    result = init_workspace_specs(tmp_path, project_title="Demo")
    assert "_project" in result.created_spec_ids
    assert "_schema" in result.created_spec_ids
    assert result.bootstrapped is True
    assert (tmp_path / ".swarm" / "specs" / "_project.md").is_file()
    assert (tmp_path / ".swarm" / "specs" / "_schema.md").is_file()


def test_init_is_idempotent(tmp_path: Path):
    init_workspace_specs(tmp_path)
    second = init_workspace_specs(tmp_path)
    assert second.created_spec_ids == ()
    assert second.bootstrapped is False


def test_init_creates_module_when_requested(tmp_path: Path):
    result = init_workspace_specs(
        tmp_path,
        initial_module_spec_id="auth/password",
        initial_module_title="Password",
    )
    assert "auth/password" in result.created_spec_ids
    assert (tmp_path / ".swarm" / "specs" / "auth" / "password.md").is_file()


def test_list_returns_sorted_specs(tmp_path: Path):
    init_workspace_specs(
        tmp_path,
        initial_module_spec_id="auth/password",
    )
    result = list_specs(tmp_path)
    assert "_project" in result.spec_ids
    assert "_schema" in result.spec_ids
    assert "auth/password" in result.spec_ids


def test_show_returns_document_and_relations(tmp_path: Path):
    init_workspace_specs(
        tmp_path,
        initial_module_spec_id="auth/password",
    )
    result = show_spec(tmp_path, "auth/password")
    assert result.document.frontmatter.spec_id == "auth/password"
    assert result.dependencies == ()
    assert result.dependants == ()


def test_show_missing_raises(tmp_path: Path):
    init_workspace_specs(tmp_path)
    with pytest.raises(SpecNotFoundError):
        show_spec(tmp_path, "does/not/exist")


def test_show_raises_when_sibling_spec_is_malformed(tmp_path: Path):
    """A malformed sibling must NOT be silently skipped while computing
    dependants — that would mean returning a wrong-but-plausible result
    (e.g. missing dependants) with no signal to the operator
    (docs/review-rules.md §2).
    """
    from backend.App.spec.infrastructure.spec_repository_fs import (
        SpecRepositoryError,
    )

    init_workspace_specs(tmp_path, initial_module_spec_id="auth/password")
    bad = tmp_path / ".swarm" / "specs" / "broken.md"
    bad.write_text("no frontmatter\n", encoding="utf-8")
    with pytest.raises(SpecRepositoryError):
        show_spec(tmp_path, "auth/password")
