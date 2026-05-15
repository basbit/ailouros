from __future__ import annotations

import json
from pathlib import Path

from backend.App.spec.domain.spec_document import SpecDocument
from backend.App.spec.domain.spec_graph import (
    SpecGraph,
    ancestors,
    build_graph,
    dependants,
    orphan_specs,
)
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
)

_GRAPH_FILENAME = "specs.graph.json"


def _load_documents(workspace_root: str | Path) -> tuple[SpecDocument, ...]:
    repository = FilesystemSpecRepository(workspace_root)
    documents = [repository.load(spec_id) for spec_id in repository.list_specs()]
    return tuple(documents)


def build_workspace_graph(workspace_root: str | Path) -> SpecGraph:
    documents = _load_documents(workspace_root)
    return build_graph(documents)


def write_graph_file(workspace_root: str | Path) -> Path:
    graph = build_workspace_graph(workspace_root)
    target_dir = Path(workspace_root).expanduser().resolve() / ".swarm"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / _GRAPH_FILENAME
    target_path.write_text(
        json.dumps(graph.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target_path


def spec_ancestors(
    workspace_root: str | Path,
    spec_id: str,
    *,
    depth: int = 1,
) -> tuple[str, ...]:
    graph = build_workspace_graph(workspace_root)
    return ancestors(graph, spec_id, depth=depth)


def spec_dependants(
    workspace_root: str | Path,
    spec_id: str,
    *,
    depth: int = 1,
) -> tuple[str, ...]:
    graph = build_workspace_graph(workspace_root)
    return dependants(graph, spec_id, depth=depth)


def spec_orphans(
    workspace_root: str | Path,
    *,
    anchor: str = "_project",
) -> tuple[str, ...]:
    graph = build_workspace_graph(workspace_root)
    return orphan_specs(graph, anchor=anchor)


__all__ = [
    "build_workspace_graph",
    "spec_ancestors",
    "spec_dependants",
    "spec_orphans",
    "write_graph_file",
]
