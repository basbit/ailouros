from __future__ import annotations

import pytest

from backend.App.spec.application.docs_writer import (
    list_archived_versions,
    write_document,
)
from backend.App.spec.application.document_graph_service import (
    read_graph,
)


@pytest.fixture()
def workspace(tmp_path):
    return tmp_path


def test_write_document_creates_file_with_frontmatter(workspace):
    target = write_document(
        workspace,
        agent="pm",
        step_id="pm",
        spec_id="pm/intent",
        body="Hello body.",
        produces=["schema:User"],
    )
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "spec_id: pm/intent" in text
    assert "version: 1" in text
    assert "produces:\n  - schema:User" in text
    assert "Hello body." in text


def test_write_document_rebuilds_graph(workspace):
    write_document(
        workspace, agent="pm", step_id="pm", spec_id="pm", body="b1"
    )
    write_document(
        workspace,
        agent="ba",
        step_id="ba",
        spec_id="ba",
        body="b2",
        depends_on=["pm"],
        produces=["schema:User"],
    )
    graph = read_graph(workspace)
    assert graph is not None
    spec_ids = sorted(node["spec_id"] for node in graph["nodes"])
    assert spec_ids == ["ba", "pm"]
    assert "schema:User" in graph["produces"]
    assert graph["produces"]["schema:User"] == ["ba"]


def test_write_document_archives_previous_version(workspace):
    write_document(workspace, agent="pm", step_id="pm", spec_id="pm", body="v1")
    write_document(workspace, agent="pm", step_id="pm", spec_id="pm", body="v2")
    archived = list_archived_versions(workspace, agent="pm", step_id="pm")
    assert len(archived) == 1
    assert "v1" in archived[0].read_text(encoding="utf-8")
    current = (workspace / ".swarm" / "docs" / "pm" / "pm.md").read_text(
        encoding="utf-8"
    )
    assert "version: 2" in current
    assert "v2" in current


def test_write_document_rejects_blank_agent(workspace):
    with pytest.raises(ValueError):
        write_document(workspace, agent="  ", step_id="x", spec_id="y", body="b")


def test_write_document_normalises_segment_slashes(workspace):
    target = write_document(
        workspace,
        agent="auth/login",
        step_id="auth/intent",
        spec_id="auth/login",
        body="body",
    )
    assert target.parts[-2:] == ("auth_login", "auth_intent.md")
