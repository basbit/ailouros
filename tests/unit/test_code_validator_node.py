from __future__ import annotations

from backend.App.orchestration.application.nodes.code_validator import (
    code_validator_node,
)
from backend.App.spec.application.docs_writer import write_document


def test_code_validator_pass_when_consumer_exists(tmp_path):
    write_document(
        tmp_path,
        agent="ba",
        step_id="ba",
        spec_id="ba",
        body="b1",
        produces=["schema:UserAccount"],
    )
    code_path = tmp_path / "src" / "models.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_text("class UserAccount:\n    pass\n", encoding="utf-8")
    delta = code_validator_node({"workspace_root": str(tmp_path)})
    assert delta["code_validator_report"]["verdict"] == "pass"


def test_code_validator_fail_when_no_consumer(tmp_path):
    write_document(
        tmp_path,
        agent="ba",
        step_id="ba",
        spec_id="ba",
        body="b1",
        produces=["schema:GhostEntity"],
    )
    delta = code_validator_node({"workspace_root": str(tmp_path)})
    report = delta["code_validator_report"]
    assert report["verdict"] == "fail"
    assert any(f["identifier"] == "GhostEntity" for f in report["findings"])


def test_code_validator_skips_without_workspace():
    delta = code_validator_node({})
    assert delta["code_validator_output"].startswith("skipped")


def test_code_validator_pass_when_no_produces(tmp_path):
    write_document(tmp_path, agent="pm", step_id="pm", spec_id="pm", body="b")
    delta = code_validator_node({"workspace_root": str(tmp_path)})
    assert delta["code_validator_report"]["verdict"] == "pass"
