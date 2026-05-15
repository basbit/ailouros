from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.App.spec.application.spec_templates import (
    module_seed_document,
    project_seed_document,
    schema_reference_document,
)
from backend.App.spec.domain.spec_document import SpecDocument
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
    SpecAlreadyExistsError,
)


@dataclass(frozen=True)
class InitResult:
    workspace_root: Path
    specs_root: Path
    created_spec_ids: tuple[str, ...]
    bootstrapped: bool


@dataclass(frozen=True)
class ListResult:
    spec_ids: tuple[str, ...]


@dataclass(frozen=True)
class ShowResult:
    document: SpecDocument
    dependencies: tuple[str, ...]
    dependants: tuple[str, ...]


def init_workspace_specs(
    workspace_root: str | Path,
    *,
    project_title: str = "Project",
    project_summary: str = "",
    initial_module_spec_id: Optional[str] = None,
    initial_module_title: str = "",
) -> InitResult:
    repository = FilesystemSpecRepository(workspace_root)
    repository.ensure_initialised()

    created: list[str] = []
    bootstrapped = False

    if not repository.exists("_project"):
        repository.save(project_seed_document(
            title=project_title, summary=project_summary,
        ), overwrite=False)
        created.append("_project")
        bootstrapped = True

    if not repository.exists("_schema"):
        repository.save(schema_reference_document(), overwrite=False)
        created.append("_schema")
        bootstrapped = True

    if initial_module_spec_id and not repository.exists(initial_module_spec_id):
        try:
            repository.save(
                module_seed_document(
                    spec_id=initial_module_spec_id,
                    title=initial_module_title,
                ),
                overwrite=False,
            )
            created.append(initial_module_spec_id)
        except SpecAlreadyExistsError:
            pass

    return InitResult(
        workspace_root=repository._workspace_root,
        specs_root=repository.specs_root,
        created_spec_ids=tuple(created),
        bootstrapped=bootstrapped,
    )


def list_specs(workspace_root: str | Path) -> ListResult:
    repository = FilesystemSpecRepository(workspace_root)
    return ListResult(spec_ids=tuple(repository.list_specs()))


def show_spec(workspace_root: str | Path, spec_id: str) -> ShowResult:
    repository = FilesystemSpecRepository(workspace_root)
    document = repository.load(spec_id)

    dependants: list[str] = []
    for other_id in repository.list_specs():
        if other_id == spec_id:
            continue
        other = repository.load(other_id)
        if spec_id in other.frontmatter.depends_on:
            dependants.append(other_id)

    return ShowResult(
        document=document,
        dependencies=document.frontmatter.depends_on,
        dependants=tuple(sorted(dependants)),
    )


__all__ = [
    "InitResult",
    "ListResult",
    "ShowResult",
    "init_workspace_specs",
    "list_specs",
    "show_spec",
]
