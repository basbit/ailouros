from __future__ import annotations

from backend.App.orchestration.application.nodes.docs_validator import (
    docs_validator_node,
)
from backend.App.spec.application.docs_writer import write_document


def test_docs_validator_returns_pass_when_graph_clean(tmp_path):
    write_document(tmp_path, agent="pm", step_id="pm", spec_id="pm", body="b1")
    state = {"workspace_root": str(tmp_path)}
    delta = docs_validator_node(state)
    assert "verdict: pass" in delta["docs_validator_output"]
    assert delta["docs_validator_report"]["verdict"] == "pass"


def test_docs_validator_returns_fail_on_dangling_reference(tmp_path):
    write_document(
        tmp_path,
        agent="ba",
        step_id="ba",
        spec_id="ba",
        body="b1",
        depends_on=["missing"],
    )
    state = {"workspace_root": str(tmp_path)}
    delta = docs_validator_node(state)
    assert "verdict: fail" in delta["docs_validator_output"]
    assert delta["docs_validator_report"]["verdict"] == "fail"
    assert any(
        f["check"] == "dangling_references"
        for f in delta["docs_validator_report"]["findings"]
    )


def test_docs_validator_skips_when_no_workspace():
    delta = docs_validator_node({})
    assert delta["docs_validator_output"].startswith("skipped")
