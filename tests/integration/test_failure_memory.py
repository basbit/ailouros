"""Tests for H-7 FailureMemory (failure_memory.py)."""
from __future__ import annotations

import json
from pathlib import Path

from backend.App.integrations.infrastructure.failure_memory import (
    failure_memory_enabled,
    failure_memory_path_for_state,
    record_failure,
    get_warnings_for,
    format_failure_warnings_block,
    _fingerprint,
    _token_score,
)


# ---------------------------------------------------------------------------
# failure_memory_enabled
# ---------------------------------------------------------------------------

def test_enabled_default(monkeypatch):
    monkeypatch.delenv("SWARM_FAILURE_MEMORY", raising=False)
    assert failure_memory_enabled() is True


def test_disabled_by_0(monkeypatch):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "0")
    assert failure_memory_enabled() is False


def test_disabled_by_false(monkeypatch):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "false")
    assert failure_memory_enabled() is False


def test_enabled_explicit_1(monkeypatch):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "1")
    assert failure_memory_enabled() is True


# ---------------------------------------------------------------------------
# failure_memory_path_for_state
# ---------------------------------------------------------------------------

def test_path_from_env(monkeypatch, tmp_path):
    target = str(tmp_path / "fm.json")
    monkeypatch.setenv("SWARM_FAILURE_MEMORY_PATH", target)
    state: dict = {}
    result = failure_memory_path_for_state(state)
    assert result == Path(target).resolve()


def test_path_from_agent_config(monkeypatch, tmp_path):
    monkeypatch.delenv("SWARM_FAILURE_MEMORY_PATH", raising=False)
    p = str(tmp_path / "custom_fm.json")
    state = {"agent_config": {"swarm": {"failure_memory_path": p}}}
    result = failure_memory_path_for_state(state)
    assert result == Path(p).resolve()


def test_path_default_fallback(monkeypatch):
    monkeypatch.delenv("SWARM_FAILURE_MEMORY_PATH", raising=False)
    result = failure_memory_path_for_state({})
    assert result.name == "failure_memory.json"
    assert ".swarm" in str(result)


# ---------------------------------------------------------------------------
# _fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_length():
    fp = _fingerprint("dev_lead", "missing must_exist_files")
    assert len(fp) == 16


def test_fingerprint_deterministic():
    fp1 = _fingerprint("qa", "NEEDS_WORK after 3 rounds")
    fp2 = _fingerprint("qa", "NEEDS_WORK after 3 rounds")
    assert fp1 == fp2


def test_fingerprint_different_for_different_inputs():
    fp1 = _fingerprint("dev", "error A")
    fp2 = _fingerprint("dev", "error B")
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# _token_score
# ---------------------------------------------------------------------------

def test_token_score_zero_for_unrelated():
    score = _token_score("authentication login oauth", "file upload image compression")
    assert score == 0.0


def test_token_score_positive_for_overlap():
    score = _token_score("missing must_exist_files deliverables", "must_exist_files not found in response")
    assert score > 0.0


def test_token_score_higher_for_more_overlap():
    s1 = _token_score("missing deliverables files", "missing deliverables")
    s2 = _token_score("missing deliverables files", "random unrelated content")
    assert s1 > s2


# ---------------------------------------------------------------------------
# record_failure + get_warnings_for (integration via tmp file)
# ---------------------------------------------------------------------------

def _state_with_path(path: Path) -> dict:
    return {"agent_config": {"swarm": {"failure_memory_path": str(path)}}}


def test_record_failure_creates_file(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files")
    assert fm_path.exists()


def test_record_failure_stores_entry(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files", context="some prompt text")
    data = json.loads(fm_path.read_text())
    assert len(data["failures"]) == 1
    entry = data["failures"][0]
    assert entry["step"] == "dev_lead"
    assert "must_exist_files" in entry["summary"]
    assert entry["count"] == 1


def test_record_failure_increments_count_on_repeat(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files")
    record_failure(state, "dev_lead", "missing must_exist_files")
    data = json.loads(fm_path.read_text())
    assert len(data["failures"]) == 1  # deduplicated
    assert data["failures"][0]["count"] == 2


def test_record_failure_stores_multiple_distinct(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev", "error A")
    record_failure(state, "dev", "error B")
    data = json.loads(fm_path.read_text())
    assert len(data["failures"]) == 2


def test_record_failure_no_op_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "0")
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev", "some error")
    assert not fm_path.exists()


def test_get_warnings_for_returns_matching(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files deliverables", context="deliverables section required")
    warnings = get_warnings_for(state, "build a plan with must_exist_files deliverables", step="dev_lead")
    assert len(warnings) >= 1
    assert "must_exist_files" in warnings[0]["summary"]


def test_get_warnings_for_returns_empty_for_unrelated(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev", "authentication oauth token error")
    warnings = get_warnings_for(state, "compress images and resize thumbnails", step="dev")
    assert warnings == []


def test_get_warnings_for_filters_by_step(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files deliverables")
    warnings = get_warnings_for(state, "must_exist_files deliverables", step="qa")
    assert warnings == []


def test_get_warnings_for_empty_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "0")
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    warnings = get_warnings_for(state, "any query")
    assert warnings == []


def test_get_warnings_for_returns_empty_on_missing_file(tmp_path):
    fm_path = tmp_path / "nonexistent_fm.json"
    state = _state_with_path(fm_path)
    warnings = get_warnings_for(state, "any query")
    assert warnings == []


# ---------------------------------------------------------------------------
# format_failure_warnings_block
# ---------------------------------------------------------------------------

def test_format_failure_warnings_block_empty_when_no_match(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    result = format_failure_warnings_block(state, "unrelated prompt xyz")
    assert result == ""


def test_format_failure_warnings_block_contains_summary(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev_lead", "missing must_exist_files deliverables")
    result = format_failure_warnings_block(state, "must_exist_files deliverables plan", step="dev_lead")
    assert "must_exist_files" in result


def test_format_failure_warnings_block_shows_count(tmp_path):
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "qa", "NEEDS_WORK after max rounds missing evidence")
    record_failure(state, "qa", "NEEDS_WORK after max rounds missing evidence")
    result = format_failure_warnings_block(state, "NEEDS_WORK rounds evidence", step="qa")
    assert "2×" in result


def test_format_failure_warnings_block_disabled_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY", "0")
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    result = format_failure_warnings_block(state, "any query")
    assert result == ""


def test_format_failure_warnings_block_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_FAILURE_MEMORY_WARN_LIMIT", "1")
    fm_path = tmp_path / "fm.json"
    state = _state_with_path(fm_path)
    record_failure(state, "dev", "error alpha beta gamma delta")
    record_failure(state, "dev", "error beta gamma delta epsilon")
    # Both should score, but limit=1 means only one warning shown
    result = format_failure_warnings_block(state, "alpha beta gamma delta epsilon", step="dev", limit=1)
    assert result.count("WARNING") == 1
