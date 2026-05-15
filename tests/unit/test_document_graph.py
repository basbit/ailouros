from __future__ import annotations

import textwrap

import pytest

from backend.App.spec.domain.document_graph import (
    DocumentParseError,
    build_graph,
    parse_document,
    validate_graph,
)


def _doc(frontmatter: str, body: str = "Body text") -> str:
    return f"---\n{textwrap.dedent(frontmatter).strip()}\n---\n{body}\n"


def test_parse_minimal_document():
    text = _doc(
        """
        spec_id: pm/intent
        agent: pm
        step_id: pm
        version: 1
        """
    )
    node = parse_document(text, path="pm/pm.md")
    assert node.spec_id == "pm/intent"
    assert node.agent == "pm"
    assert node.depends_on == ()
    assert node.spec_hash.startswith("sha256:")


def test_parse_rejects_missing_frontmatter():
    with pytest.raises(DocumentParseError):
        parse_document("plain markdown without frontmatter", path="x.md")


def test_parse_rejects_missing_spec_id():
    with pytest.raises(DocumentParseError):
        parse_document(_doc("agent: pm\nstep_id: pm\nversion: 1"), path="x.md")


def test_build_graph_creates_edges_from_depends_on():
    pm = parse_document(_doc("spec_id: pm\nagent: pm\nstep_id: pm\nversion: 1"), path="pm.md")
    ba = parse_document(
        _doc(
            """
            spec_id: ba
            agent: ba
            step_id: ba
            version: 1
            depends_on:
              - pm
            produces:
              - schema:User
            """
        ),
        path="ba.md",
    )
    graph = build_graph([pm, ba])
    assert len(graph.edges) == 1
    assert graph.edges[0].from_spec == "ba"
    assert graph.edges[0].to_spec == "pm"
    assert graph.produces_index["schema:User"] == ("ba",)


def test_validate_flags_dangling_reference():
    orphan = parse_document(
        _doc("spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1\ndepends_on: [pm]"),
        path="ba.md",
    )
    report = validate_graph(build_graph([orphan]))
    assert report.has_errors
    assert any(f.check == "dangling_references" for f in report.findings)


def test_validate_flags_unresolved_clarifications():
    node = parse_document(
        _doc("spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1", "NEEDS_CLARIFICATION pending"),
        path="ba.md",
    )
    report = validate_graph(build_graph([node]))
    assert report.has_errors
    assert any(f.check == "unresolved_clarifications" for f in report.findings)


def test_validate_flags_duplicate_definitions():
    a = parse_document(
        _doc("spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1\nproduces: [schema:User]"),
        path="ba.md",
    )
    b = parse_document(
        _doc(
            "spec_id: arch\nagent: architect\nstep_id: architect\nversion: 1\nproduces: [schema:User]"
        ),
        path="arch.md",
    )
    report = validate_graph(build_graph([a, b]))
    assert report.has_errors
    assert any(f.check == "duplicate_definitions" for f in report.findings)


def test_validate_warns_on_ambiguity_markers():
    node = parse_document(
        _doc("spec_id: ba\nagent: ba\nstep_id: ba\nversion: 1", "TODO clarify deadlines"),
        path="ba.md",
    )
    report = validate_graph(build_graph([node]))
    assert not report.has_errors
    assert any(f.check == "ambiguity" and f.severity == "warning" for f in report.findings)


def test_clean_graph_verdict_pass():
    pm = parse_document(_doc("spec_id: pm\nagent: pm\nstep_id: pm\nversion: 1"), path="pm.md")
    report = validate_graph(build_graph([pm]))
    assert report.verdict == "pass"
    assert not report.has_errors
