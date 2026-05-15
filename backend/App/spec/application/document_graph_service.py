from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from backend.App.spec.domain.document_graph import (
    DocumentGraph,
    DocumentNode,
    DocumentParseError,
    ValidationFinding,
    ValidationReport,
    build_graph,
    parse_document,
    validate_graph,
)

_DOCS_SUBDIR = ".swarm/docs"
_GRAPH_FILE = "_graph.json"


def docs_root_for(workspace_root: Path) -> Path:
    return (workspace_root / _DOCS_SUBDIR).resolve()


def graph_path_for(workspace_root: Path) -> Path:
    return docs_root_for(workspace_root) / _GRAPH_FILE


def _iter_document_files(docs_root: Path):
    if not docs_root.is_dir():
        return
    for path in sorted(docs_root.rglob("*.md")):
        if path.name.startswith("_"):
            continue
        yield path


def load_documents(workspace_root: Path) -> list[DocumentNode]:
    docs_root = docs_root_for(workspace_root)
    nodes: list[DocumentNode] = []
    for path in _iter_document_files(docs_root):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(docs_root))
        nodes.append(parse_document(text, path=relative))
    return nodes


def build_workspace_graph(workspace_root: Path) -> DocumentGraph:
    return build_graph(load_documents(workspace_root))


def write_graph(workspace_root: Path, graph: DocumentGraph) -> Path:
    target = graph_path_for(workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def read_graph(workspace_root: Path) -> Optional[dict]:
    target = graph_path_for(workspace_root)
    if not target.is_file():
        return None
    raw = target.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{target}: graph file must contain a JSON object")
    return data


def validate_workspace_documents(workspace_root: Path) -> ValidationReport:
    try:
        graph = build_workspace_graph(workspace_root)
    except DocumentParseError as exc:
        return ValidationReport(
            findings=(
                ValidationFinding(
                    check="parse_error",
                    severity="error",
                    spec_id="",
                    detail=str(exc),
                ),
            )
        )
    return validate_graph(graph)


__all__ = [
    "build_workspace_graph",
    "docs_root_for",
    "graph_path_for",
    "load_documents",
    "read_graph",
    "validate_workspace_documents",
    "write_graph",
]
