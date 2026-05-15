from __future__ import annotations

import pytest
from pathlib import Path

from backend.App.repomap.infrastructure.treesitter_extractor import (
    extract_symbols,
    signatures_by_file,
    _ts_available,
)


pytestmark = pytest.mark.skipif(
    not _ts_available(), reason="tree-sitter-language-pack not installed"
)


def test_extract_python_functions(tmp_path: Path):
    (tmp_path / "mod.py").write_text(
        "def alpha():\n    pass\n\ndef beta():\n    pass\n",
        encoding="utf-8",
    )
    graph = extract_symbols(tmp_path)
    names = {n.name for n in graph.nodes}
    assert "alpha" in names
    assert "beta" in names


def test_extract_python_class(tmp_path: Path):
    (tmp_path / "mod.py").write_text(
        "class MyService:\n    def method(self):\n        pass\n",
        encoding="utf-8",
    )
    graph = extract_symbols(tmp_path)
    kinds = {n.kind for n in graph.nodes}
    names = {n.name for n in graph.nodes}
    assert "class" in kinds
    assert "MyService" in names


def test_extract_typescript_symbols(tmp_path: Path):
    (tmp_path / "svc.ts").write_text(
        "export function greet(name: string): string { return name; }\n"
        "export class Greeter { say() {} }\n",
        encoding="utf-8",
    )
    graph = extract_symbols(tmp_path)
    names = {n.name for n in graph.nodes}
    assert "greet" in names
    assert "Greeter" in names


def test_ignores_non_source_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "util.py").write_text("def util(): pass\n", encoding="utf-8")
    graph = extract_symbols(tmp_path)
    file_paths = {n.file_path for n in graph.nodes}
    assert all(fp.endswith(".py") for fp in file_paths)


def test_empty_workspace_returns_empty_graph(tmp_path: Path):
    graph = extract_symbols(tmp_path)
    assert graph.nodes == ()
    assert graph.edges == ()


def test_cross_file_edges_built(tmp_path: Path):
    (tmp_path / "a.py").write_text("def shared_fn(): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import shared_fn\nshared_fn()\n", encoding="utf-8")
    graph = extract_symbols(tmp_path)
    edge_pairs = {(e.from_node_path, e.to_node_path) for e in graph.edges}
    assert ("b.py", "a.py") in edge_pairs


def test_signatures_by_file_groups_correctly(tmp_path: Path):
    (tmp_path / "grp.py").write_text(
        "def foo(): pass\ndef bar(): pass\nclass Baz: pass\n",
        encoding="utf-8",
    )
    graph = extract_symbols(tmp_path)
    sigs = signatures_by_file(graph.nodes)
    assert "grp.py" in sigs
    assert len(sigs["grp.py"]) == 3


def test_ignore_dirs_respected(tmp_path: Path):
    node_mod = tmp_path / "node_modules"
    node_mod.mkdir()
    (node_mod / "lib.py").write_text("def hidden(): pass\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def visible(): pass\n", encoding="utf-8")
    graph = extract_symbols(tmp_path)
    file_paths = {n.file_path for n in graph.nodes}
    assert not any("node_modules" in fp for fp in file_paths)
    assert any("real.py" in fp for fp in file_paths)
