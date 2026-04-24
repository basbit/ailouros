"""Tests for M-9 output compression contracts (output_contracts.py)."""
from __future__ import annotations

import json

from backend.App.orchestration.application.contracts.output_contracts import (
    CompressedDevLeadOutput,
    CompressedQASummary,
    CompressedReviewerOutput,
    compress_dev_lead_output,
    compress_qa_output,
    compress_reviewer_output,
    format_compressed_dev_lead,
    format_compressed_qa,
    format_compressed_reviewer,
    output_compression_enabled,
    reviewer_compact_for_prompt,
)
from backend.App.orchestration.application.context.delta_prompt import resolve_artifact


# ---------------------------------------------------------------------------
# output_compression_enabled
# ---------------------------------------------------------------------------

def test_output_compression_enabled_default(monkeypatch):
    monkeypatch.delenv("SWARM_OUTPUT_COMPRESSION", raising=False)
    assert output_compression_enabled() is True


def test_output_compression_disabled_by_0(monkeypatch):
    monkeypatch.setenv("SWARM_OUTPUT_COMPRESSION", "0")
    assert output_compression_enabled() is False


def test_output_compression_disabled_by_false(monkeypatch):
    monkeypatch.setenv("SWARM_OUTPUT_COMPRESSION", "false")
    assert output_compression_enabled() is False


def test_output_compression_disabled_by_off(monkeypatch):
    monkeypatch.setenv("SWARM_OUTPUT_COMPRESSION", "off")
    assert output_compression_enabled() is False


def test_output_compression_enabled_explicit_1(monkeypatch):
    monkeypatch.setenv("SWARM_OUTPUT_COMPRESSION", "1")
    assert output_compression_enabled() is True


# ---------------------------------------------------------------------------
# compress_reviewer_output
# ---------------------------------------------------------------------------

REVIEWER_OK = (
    "The implementation looks good.\n"
    "All checklist items pass.\n"
    "<defect_report>{\"defects\":[]}</defect_report>\n"
    "VERDICT: OK\n"
)

REVIEWER_NEEDS_WORK = (
    "Several issues found.\n"
    '<defect_report>{"defects":['
    '{"id":"D1","title":"Missing error handler","severity":"P1","file_paths":["app.py"],'
    '"expected":"error handler","actual":"none","repro_steps":[],"acceptance":[],"category":"bug","fixed":false},'
    '{"id":"D2","title":"Wrong endpoint path","severity":"P0","file_paths":["routes.py"],'
    '"expected":"/api/v1/x","actual":"/x","repro_steps":[],"acceptance":[],"category":"bug","fixed":false}'
    ']}</defect_report>\n'
    "VERDICT: NEEDS_WORK\n"
)


def test_compress_reviewer_output_verdict_ok():
    c = compress_reviewer_output(REVIEWER_OK)
    assert isinstance(c, CompressedReviewerOutput)
    assert c.verdict == "OK"


def test_compress_reviewer_output_verdict_needs_work():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    assert c.verdict == "NEEDS_WORK"


def test_compress_reviewer_output_defect_count():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    assert c.defect_count == 2


def test_compress_reviewer_output_defects_inline():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    assert len(c.defects) <= 5
    assert any(d.get("title") == "Missing error handler" for d in c.defects)


def test_compress_reviewer_output_stores_artifact():
    c = compress_reviewer_output(REVIEWER_OK)
    assert c.artifact_ref.startswith("artifact:sha256:")
    resolved = resolve_artifact(c.artifact_ref)
    assert resolved == REVIEWER_OK


def test_compress_reviewer_output_char_count():
    c = compress_reviewer_output(REVIEWER_OK)
    assert c.char_count == len(REVIEWER_OK)


def test_compress_reviewer_output_summary_present():
    c = compress_reviewer_output(REVIEWER_OK)
    assert len(c.summary) > 0
    assert len(c.summary) <= 300


def test_compress_reviewer_output_no_verdict_defaults_needs_work():
    c = compress_reviewer_output("Some review text with no verdict line.")
    assert c.verdict == "NEEDS_WORK"


def test_compress_reviewer_output_top5_cap():
    """More than 5 defects → inline list capped at 5."""
    defects_json = json.dumps([
        {"id": f"D{i}", "title": f"Defect {i}", "severity": "P1",
         "file_paths": [], "expected": "", "actual": "",
         "repro_steps": [], "acceptance": [], "category": "bug", "fixed": False}
        for i in range(8)
    ])
    text = f"<defect_report>{{\"defects\":{defects_json}}}</defect_report>\nVERDICT: NEEDS_WORK\n"
    c = compress_reviewer_output(text)
    assert c.defect_count == 8
    assert len(c.defects) == 5


# ---------------------------------------------------------------------------
# format_compressed_reviewer
# ---------------------------------------------------------------------------

def test_format_compressed_reviewer_is_valid_json():
    c = compress_reviewer_output(REVIEWER_OK)
    s = format_compressed_reviewer(c)
    data = json.loads(s)
    assert data["verdict"] == "OK"
    assert "artifact_ref" in data
    assert "defect_count" in data
    assert "summary" in data


def test_format_compressed_reviewer_includes_defects():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    s = format_compressed_reviewer(c)
    data = json.loads(s)
    assert isinstance(data["defects"], list)
    assert len(data["defects"]) > 0


# ---------------------------------------------------------------------------
# reviewer_compact_for_prompt
# ---------------------------------------------------------------------------

def test_reviewer_compact_for_prompt_contains_verdict():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    prompt_text = reviewer_compact_for_prompt(c)
    assert "NEEDS_WORK" in prompt_text


def test_reviewer_compact_for_prompt_shows_top_defects():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    prompt_text = reviewer_compact_for_prompt(c)
    assert "Missing error handler" in prompt_text


def test_reviewer_compact_for_prompt_shows_defect_count():
    c = compress_reviewer_output(REVIEWER_NEEDS_WORK)
    prompt_text = reviewer_compact_for_prompt(c)
    assert "2" in prompt_text  # defect count


def test_reviewer_compact_for_prompt_no_full_text(monkeypatch):
    """Prompt embedding must not include full reviewer prose verbatim."""
    long_review = "UNIQUE_REVIEW_MARKER " + "x" * 2000 + "\nVERDICT: OK\n"
    c = compress_reviewer_output(long_review)
    prompt_text = reviewer_compact_for_prompt(c)
    assert "UNIQUE_REVIEW_MARKER " + "x" * 2000 not in prompt_text


# ---------------------------------------------------------------------------
# compress_dev_lead_output
# ---------------------------------------------------------------------------

DEV_LEAD_OUTPUT = json.dumps({
    "tasks": [
        {"id": "T1", "description": "Implement auth", "agent": "dev"},
        {"id": "T2", "description": "Write tests", "agent": "qa"},
    ],
    "deliverables": {
        "must_exist_files": ["app/auth.py"],
        "spec_symbols": ["AuthService"],
        "verification_commands": ["pytest tests/"],
        "assumptions": ["PostgreSQL 14"],
    }
})


def test_compress_dev_lead_output_returns_dataclass():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert isinstance(c, CompressedDevLeadOutput)


def test_compress_dev_lead_output_tasks_extracted():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert isinstance(c.tasks, list)
    assert len(c.tasks) >= 1


def test_compress_dev_lead_output_deliverables_extracted():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert isinstance(c.deliverables, dict)


def test_compress_dev_lead_output_stores_artifact():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert c.artifact_ref.startswith("artifact:sha256:")
    resolved = resolve_artifact(c.artifact_ref)
    assert resolved == DEV_LEAD_OUTPUT


def test_compress_dev_lead_output_char_count():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert c.char_count == len(DEV_LEAD_OUTPUT)


def test_compress_dev_lead_output_summary_capped():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert len(c.summary) <= 200


# ---------------------------------------------------------------------------
# format_compressed_dev_lead
# ---------------------------------------------------------------------------

def test_format_compressed_dev_lead_is_valid_json():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    s = format_compressed_dev_lead(c)
    data = json.loads(s)
    assert "tasks" in data
    assert "deliverables" in data
    assert "artifact_ref" in data
    assert "char_count" in data


def test_format_compressed_dev_lead_tasks_list():
    c = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    s = format_compressed_dev_lead(c)
    data = json.loads(s)
    assert isinstance(data["tasks"], list)


# ---------------------------------------------------------------------------
# compress_qa_output
# ---------------------------------------------------------------------------

QA_OK = "All tests passed.\nVERDICT: OK\n"
QA_PASS = "Tests complete.\nVERDICT: PASS\n"
QA_FAIL = "Tests failed.\nVERDICT: FAIL\n"
QA_NEEDS_WORK = "Some tests failed.\nVERDICT: NEEDS_WORK\n"


def test_compress_qa_output_verdict_ok():
    c = compress_qa_output(QA_OK)
    assert isinstance(c, CompressedQASummary)
    assert c.verdict == "OK"


def test_compress_qa_output_verdict_pass_normalised():
    """PASS verdict is normalised to OK."""
    c = compress_qa_output(QA_PASS)
    assert c.verdict == "OK"


def test_compress_qa_output_verdict_fail_normalised():
    """FAIL verdict is normalised to NEEDS_WORK."""
    c = compress_qa_output(QA_FAIL)
    assert c.verdict == "NEEDS_WORK"


def test_compress_qa_output_verdict_needs_work():
    c = compress_qa_output(QA_NEEDS_WORK)
    assert c.verdict == "NEEDS_WORK"


def test_compress_qa_output_no_verdict_defaults_needs_work():
    c = compress_qa_output("No verdict in this output.")
    assert c.verdict == "NEEDS_WORK"


def test_compress_qa_output_stores_artifact():
    c = compress_qa_output(QA_OK)
    assert c.artifact_ref.startswith("artifact:sha256:")
    resolved = resolve_artifact(c.artifact_ref)
    assert resolved == QA_OK


def test_compress_qa_output_char_count():
    c = compress_qa_output(QA_OK)
    assert c.char_count == len(QA_OK)


def test_compress_qa_output_summary_capped():
    long_qa = "x" * 1000 + "\nVERDICT: OK\n"
    c = compress_qa_output(long_qa)
    assert len(c.summary) <= 400


# ---------------------------------------------------------------------------
# format_compressed_qa
# ---------------------------------------------------------------------------

def test_format_compressed_qa_is_valid_json():
    c = compress_qa_output(QA_OK)
    s = format_compressed_qa(c)
    data = json.loads(s)
    assert data["verdict"] == "OK"
    assert "summary" in data
    assert "char_count" in data
    assert "artifact_ref" in data


def test_format_compressed_qa_round_trips():
    c = compress_qa_output(QA_NEEDS_WORK)
    s = format_compressed_qa(c)
    data = json.loads(s)
    assert data["verdict"] == "NEEDS_WORK"


# ---------------------------------------------------------------------------
# Idempotency & content-addressing across compressors
# ---------------------------------------------------------------------------

def test_compress_reviewer_idempotent():
    """Compressing the same text twice returns same artifact_ref."""
    c1 = compress_reviewer_output(REVIEWER_OK)
    c2 = compress_reviewer_output(REVIEWER_OK)
    assert c1.artifact_ref == c2.artifact_ref


def test_compress_qa_idempotent():
    c1 = compress_qa_output(QA_OK)
    c2 = compress_qa_output(QA_OK)
    assert c1.artifact_ref == c2.artifact_ref


def test_compress_dev_lead_idempotent():
    c1 = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    c2 = compress_dev_lead_output(DEV_LEAD_OUTPUT)
    assert c1.artifact_ref == c2.artifact_ref


def test_different_outputs_different_refs():
    c_ok = compress_qa_output(QA_OK)
    c_fail = compress_qa_output(QA_FAIL)
    assert c_ok.artifact_ref != c_fail.artifact_ref
