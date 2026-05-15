from __future__ import annotations

import json
from pathlib import Path

from backend.App.spec.application.graph_use_cases import (
    build_workspace_graph,
    spec_ancestors,
    spec_dependants,
    spec_orphans,
    write_graph_file,
)
from backend.App.spec.application.use_cases import init_workspace_specs
from backend.App.spec.domain.spec_document import (
    SpecDocument,
    SpecFrontmatter,
)
from backend.App.spec.domain.spec_graph import build_graph
from backend.App.spec.infrastructure.spec_repository_fs import (
    FilesystemSpecRepository,
)


def _document(
    spec_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    codegen_targets: tuple[str, ...] = (),
) -> SpecDocument:
    return SpecDocument(
        frontmatter=SpecFrontmatter(
            spec_id=spec_id,
            depends_on=depends_on,
            codegen_targets=codegen_targets,
        ),
        body="\n",
        sections=(),
    )


def test_graph_has_spec_and_code_nodes_with_generates_edges():
    graph = build_graph(
        [
            _document("auth/password", codegen_targets=("src/auth/password.py",)),
        ]
    )
    assert any(node.kind == "spec" and node.id == "auth/password" for node in graph.nodes)
    assert any(node.kind == "code" and node.id == "src/auth/password.py" for node in graph.nodes)
    assert any(
        edge.kind == "generates"
        and edge.from_id == "auth/password"
        and edge.to_id == "src/auth/password.py"
        for edge in graph.edges
    )


def test_graph_classifies_test_target_as_test_node():
    graph = build_graph(
        [
            _document("auth/password", codegen_targets=("tests/unit/test_password.py",)),
        ]
    )
    assert any(
        node.kind == "test" and node.id == "tests/unit/test_password.py"
        for node in graph.nodes
    )


def test_graph_emits_depends_on_edges():
    graph = build_graph(
        [
            _document("a", depends_on=("b",)),
            _document("b"),
        ]
    )
    assert any(
        edge.kind == "depends_on" and edge.from_id == "a" and edge.to_id == "b"
        for edge in graph.edges
    )


def test_ancestors_walks_depth():
    documents = [
        _document("a", depends_on=("b",)),
        _document("b", depends_on=("c",)),
        _document("c"),
    ]
    graph = build_graph(documents)
    assert set(spec_ancestors_for(graph, "a", depth=1)) == {"b"}
    assert set(spec_ancestors_for(graph, "a", depth=2)) == {"b", "c"}


def spec_ancestors_for(graph, spec_id, depth):
    from backend.App.spec.domain.spec_graph import ancestors as ancestors_for_graph
    return ancestors_for_graph(graph, spec_id, depth=depth)


def test_dependants_inverse_of_ancestors():
    documents = [
        _document("a", depends_on=("b",)),
        _document("b", depends_on=("c",)),
        _document("c"),
    ]
    graph = build_graph(documents)
    from backend.App.spec.domain.spec_graph import dependants as dependants_for_graph
    assert set(dependants_for_graph(graph, "c", depth=2)) == {"a", "b"}


def test_orphan_specs_finds_disconnected(tmp_path: Path):
    repository = FilesystemSpecRepository(tmp_path)
    repository.ensure_initialised()
    repository.save(_document("_project"))
    repository.save(_document("connected", depends_on=("_project",)))
    repository.save(_document("loose"))
    result = spec_orphans(tmp_path)
    assert "loose" in result
    assert "_project" not in result
    assert "connected" not in result


def test_write_graph_file_creates_json(tmp_path: Path):
    init_workspace_specs(tmp_path, initial_module_spec_id="auth/password")
    path = write_graph_file(tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text())
    spec_ids = {node["id"] for node in payload["nodes"] if node["kind"] == "spec"}
    assert "_project" in spec_ids
    assert "_schema" in spec_ids
    assert "auth/password" in spec_ids


def test_build_workspace_graph_loads_documents(tmp_path: Path):
    init_workspace_specs(tmp_path)
    graph = build_workspace_graph(tmp_path)
    assert any(node.kind == "spec" and node.id == "_project" for node in graph.nodes)


def test_spec_ancestors_returns_tuple(tmp_path: Path):
    init_workspace_specs(tmp_path)
    result = spec_ancestors(tmp_path, "_project")
    assert isinstance(result, tuple)


def test_spec_dependants_returns_tuple(tmp_path: Path):
    init_workspace_specs(tmp_path)
    result = spec_dependants(tmp_path, "_project")
    assert isinstance(result, tuple)


def test_build_workspace_graph_raises_on_malformed_spec(tmp_path: Path):
    """A malformed spec must surface as an error rather than be silently
    skipped — silent skip would make the graph wrong without telling the
    operator anything is broken (docs/review-rules.md §2).
    """
    import pytest

    from backend.App.spec.infrastructure.spec_repository_fs import (
        SpecRepositoryError,
    )

    init_workspace_specs(tmp_path)
    bad = tmp_path / ".swarm" / "specs" / "broken.md"
    bad.write_text("no frontmatter at all\n", encoding="utf-8")
    with pytest.raises(SpecRepositoryError):
        build_workspace_graph(tmp_path)
