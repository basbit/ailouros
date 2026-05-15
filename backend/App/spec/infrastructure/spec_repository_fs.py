from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecParseError,
    parse_spec,
    render_spec,
)

logger = logging.getLogger(__name__)

_SPECS_DIRNAME = ".swarm/specs"
_SPEC_SUFFIX = ".md"


class SpecRepositoryError(Exception):
    pass


class SpecNotFoundError(SpecRepositoryError):
    pass


class SpecAlreadyExistsError(SpecRepositoryError):
    pass


class FilesystemSpecRepository:
    def __init__(self, workspace_root: Path | str) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise SpecRepositoryError(
                f"workspace_root does not exist or is not a directory: {root}"
            )
        self._workspace_root = root
        self._specs_root = (root / _SPECS_DIRNAME).resolve()

    @property
    def specs_root(self) -> Path:
        return self._specs_root

    def _path_for(self, spec_id: str) -> Path:
        normalised = spec_id.strip().strip("/")
        if not normalised:
            raise SpecRepositoryError("spec_id is empty")
        if ".." in normalised.split("/"):
            raise SpecRepositoryError(f"spec_id contains parent traversal: {spec_id!r}")
        candidate = (self._specs_root / (normalised + _SPEC_SUFFIX)).resolve()
        try:
            candidate.relative_to(self._specs_root)
        except ValueError as exception:
            raise SpecRepositoryError(
                f"spec_id resolves outside specs root: {spec_id!r}"
            ) from exception
        return candidate

    def exists(self, spec_id: str) -> bool:
        try:
            return self._path_for(spec_id).is_file()
        except SpecRepositoryError:
            return False

    def load(self, spec_id: str) -> SpecDocument:
        path = self._path_for(spec_id)
        if not path.is_file():
            raise SpecNotFoundError(f"spec not found: {spec_id}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exception:
            raise SpecRepositoryError(f"failed to read spec {spec_id}: {exception}") from exception
        try:
            document = parse_spec(text)
        except SpecParseError as exception:
            raise SpecRepositoryError(f"spec {spec_id} is malformed: {exception}") from exception
        return document

    def save(self, document: SpecDocument, *, overwrite: bool = True) -> Path:
        path = self._path_for(document.frontmatter.spec_id)
        if path.exists() and not overwrite:
            raise SpecAlreadyExistsError(
                f"spec already exists: {document.frontmatter.spec_id}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_spec(document), encoding="utf-8")
        return path

    def delete(self, spec_id: str) -> None:
        path = self._path_for(spec_id)
        if path.is_file():
            path.unlink()
        else:
            raise SpecNotFoundError(f"spec not found: {spec_id}")

    def iter_spec_ids(self) -> Iterable[str]:
        if not self._specs_root.is_dir():
            return
        for path in sorted(self._specs_root.rglob("*" + _SPEC_SUFFIX)):
            relative = path.relative_to(self._specs_root)
            spec_id = relative.with_suffix("").as_posix()
            yield spec_id

    def list_specs(self) -> list[str]:
        return list(self.iter_spec_ids())

    def ensure_initialised(self) -> Path:
        self._specs_root.mkdir(parents=True, exist_ok=True)
        return self._specs_root


def resolve_workspace_specs_root(workspace_root: Optional[str | Path]) -> Path:
    if not workspace_root:
        raise SpecRepositoryError("workspace_root is required")
    return Path(workspace_root).expanduser().resolve() / _SPECS_DIRNAME


__all__ = [
    "FilesystemSpecRepository",
    "SpecAlreadyExistsError",
    "SpecNotFoundError",
    "SpecRepositoryError",
    "resolve_workspace_specs_root",
]
