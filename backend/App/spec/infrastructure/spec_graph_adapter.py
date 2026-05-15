from __future__ import annotations

from pathlib import Path

from backend.App.spec.application.graph_use_cases import spec_ancestors
from backend.App.spec.domain.ports import SpecGraphPort
from backend.App.spec.domain.spec_document import SpecDocument
from backend.App.spec.infrastructure.spec_repository_fs import FilesystemSpecRepository


class FilesystemSpecGraphAdapter(SpecGraphPort):
    def ancestors(
        self,
        workspace_root: Path,
        spec_id: str,
        *,
        depth: int,
    ) -> tuple[str, ...]:
        return spec_ancestors(workspace_root, spec_id, depth=depth)

    def load_spec(
        self,
        workspace_root: Path,
        spec_id: str,
    ) -> SpecDocument:
        repository = FilesystemSpecRepository(workspace_root)
        return repository.load(spec_id)


__all__ = ["FilesystemSpecGraphAdapter"]
