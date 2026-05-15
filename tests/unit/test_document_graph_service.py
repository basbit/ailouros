from __future__ import annotations

import textwrap
from pathlib import Path

from backend.App.spec.application.document_graph_service import (
    build_workspace_graph,
    graph_path_for,
    read_graph,
    validate_workspace_documents,
    write_graph,
)


def _write_doc(root: Path, relative: str, frontmatter: str, body: str = "Body") -> None:
    target = root / ".swarm" / "docs" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_build_workspace_graph_empty(tmp_path):
    graph = build_workspace_graph(tmp_path)
    assert graph.nodes == ()
    assert graph.edges == ()


def test_build_workspace_graph_with_dependencies(tmp_path):
    _write_doc(tmp_path, "pm/pm.md", "spec_id: pm\nagent: pm\nstep_id: pm\nversion: 1")
    _write_doc(
        tmp_path,
        "ba/ba.md",
        "spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1\ndepends_on: [pm]",
    )
    graph = build_workspace_graph(tmp_path)
    spec_ids = sorted(node.spec_id for node in graph.nodes)
    assert spec_ids == ["ba", "pm"]
    assert len(graph.edges) == 1


def test_write_and_read_graph_roundtrip(tmp_path):
    _write_doc(tmp_path, "pm/pm.md", "spec_id: pm\nagent: pm\nstep_id: pm\nversion: 1")
    graph = build_workspace_graph(tmp_path)
    target = write_graph(tmp_path, graph)
    assert target == graph_path_for(tmp_path)
    data = read_graph(tmp_path)
    assert data is not None
    assert data["nodes"][0]["spec_id"] == "pm"


def test_read_graph_returns_none_when_missing(tmp_path):
    assert read_graph(tmp_path) is None


def test_validate_workspace_documents_pass(tmp_path):
    _write_doc(tmp_path, "pm/pm.md", "spec_id: pm\nagent: pm\nstep_id: pm\nversion: 1")
    report = validate_workspace_documents(tmp_path)
    assert report.verdict == "pass"


def test_validate_workspace_documents_dangling(tmp_path):
    _write_doc(
        tmp_path,
        "ba/ba.md",
        "spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1\ndepends_on: [missing_pm]",
    )
    report = validate_workspace_documents(tmp_path)
    assert report.verdict == "fail"
    assert any(f.check == "dangling_references" for f in report.findings)


def test_validate_workspace_documents_parse_error_becomes_finding(tmp_path):
    bad = tmp_path / ".swarm" / "docs" / "broken.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("no frontmatter here", encoding="utf-8")
    report = validate_workspace_documents(tmp_path)
    assert report.verdict == "fail"
    assert any(f.check == "parse_error" for f in report.findings)
