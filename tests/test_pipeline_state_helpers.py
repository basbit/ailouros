"""Tests for pipeline_state_helpers.py — state init, compaction, feature flags, human resume."""
from __future__ import annotations

import os
import pytest

from backend.App.orchestration.application.pipeline_state_helpers import (
    _initial_pipeline_state,
    _legacy_workspace_parts_from_input,
    _set_feature_env,
    _state_snapshot,
    _state_max_chars,
    _compact_state_if_needed,
    _migrate_legacy_pm_tasks_state,
    human_pipeline_step_label,
    format_human_resume_output,
    append_step_feedback,
    get_step_retries,
    increment_step_retry,
)

_MARKER = "\n\n---\n\n# User task\n\n"


# ---------------------------------------------------------------------------
# _legacy_workspace_parts_from_input
# ---------------------------------------------------------------------------

def test_legacy_parts_no_marker():
    result = _legacy_workspace_parts_from_input("simple task text")
    assert result["user_task"] == "simple task text"
    assert result["project_manifest"] == ""
    assert result["workspace_snapshot"] == ""
    assert result["workspace_context_mode"] == "full"


def test_legacy_parts_with_marker():
    assembled = "some preamble" + _MARKER + "actual user task"
    result = _legacy_workspace_parts_from_input(assembled)
    assert result["user_task"] == "actual user task"
    assert result["project_manifest"] == ""


def test_legacy_parts_empty_tail_falls_back_to_full_input():
    assembled = "some preamble" + _MARKER + "   "
    result = _legacy_workspace_parts_from_input(assembled)
    # tail is blank — should fall back to full input stripped
    assert result["user_task"] == assembled.strip()


# ---------------------------------------------------------------------------
# _set_feature_env
# ---------------------------------------------------------------------------

def test_set_feature_env_boolean_truthy(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY_1", raising=False)
    _set_feature_env({"feat": True}, "feat", "TEST_ENV_KEY_1")
    assert os.environ.get("TEST_ENV_KEY_1") == "1"


def test_set_feature_env_boolean_falsy(monkeypatch):
    monkeypatch.setenv("TEST_ENV_KEY_2", "1")
    _set_feature_env({"feat": False}, "feat", "TEST_ENV_KEY_2")
    assert os.environ.get("TEST_ENV_KEY_2") == "0"


def test_set_feature_env_boolean_string_false(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY_3", raising=False)
    _set_feature_env({"feat": "false"}, "feat", "TEST_ENV_KEY_3")
    assert os.environ.get("TEST_ENV_KEY_3") == "0"


def test_set_feature_env_string_mode(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY_4", raising=False)
    _set_feature_env({"model": "claude-opus"}, "model", "TEST_ENV_KEY_4", is_str=True)
    assert os.environ.get("TEST_ENV_KEY_4") == "claude-opus"


def test_set_feature_env_string_empty_not_set(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY_5", raising=False)
    _set_feature_env({"model": "   "}, "model", "TEST_ENV_KEY_5", is_str=True)
    assert os.environ.get("TEST_ENV_KEY_5") is None


def test_set_feature_env_missing_key_noop(monkeypatch):
    monkeypatch.delenv("TEST_ENV_KEY_6", raising=False)
    _set_feature_env({}, "feat", "TEST_ENV_KEY_6")
    assert os.environ.get("TEST_ENV_KEY_6") is None


# ---------------------------------------------------------------------------
# _state_snapshot
# ---------------------------------------------------------------------------

def test_state_snapshot_strips_runtime_keys():
    import threading
    ev = threading.Event()
    state = {"task_id": "t1", "output": "hello", "_pipeline_cancel_event": ev}
    snap = _state_snapshot(state)
    assert "_pipeline_cancel_event" not in snap
    assert snap["task_id"] == "t1"
    assert snap["output"] == "hello"


def test_state_snapshot_deep_copy():
    state = {"nested": {"a": 1}}
    snap = _state_snapshot(state)
    snap["nested"]["a"] = 99
    assert state["nested"]["a"] == 1  # original unchanged


# ---------------------------------------------------------------------------
# _state_max_chars
# ---------------------------------------------------------------------------

def test_state_max_chars_default():
    val = _state_max_chars()
    assert val == 200_000


def test_state_max_chars_from_env(monkeypatch):
    monkeypatch.setenv("SWARM_STATE_MAX_CHARS", "50000")
    assert _state_max_chars() == 50000


def test_state_max_chars_invalid_env_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_STATE_MAX_CHARS", "not_a_number")
    assert _state_max_chars() == 200_000


def test_state_max_chars_enforces_minimum(monkeypatch):
    monkeypatch.setenv("SWARM_STATE_MAX_CHARS", "100")
    assert _state_max_chars() == 10000


def test_initial_pipeline_state_rejects_workspace_identity_mismatch():
    with pytest.raises(ValueError, match="workspace identity mismatch"):
        _initial_pipeline_state(
            "task",
            {},
            workspace_root="/runtime/repo",
            pipeline_workspace_parts={
                "user_task": "task",
                "workspace_root_resolved": "/other/repo",
            },
        )


# ---------------------------------------------------------------------------
# _compact_state_if_needed
# ---------------------------------------------------------------------------

def test_compact_state_no_compaction_needed():
    state = {"task_id": "t1", "user_task": "hello"}
    result = _compact_state_if_needed(state, "pm")
    assert result is None


def test_compact_state_compacts_large_state(monkeypatch):
    # _state_max_chars enforces min=10000; build a state larger than that
    monkeypatch.setenv("SWARM_STATE_MAX_CHARS", "10001")
    # arch_review_output is in _COMPACTION_SUMMARISE_KEYS
    large_value = "A" * 11000  # this alone exceeds 10001
    state = {
        "task_id": "t1",
        "user_task": "do something",
        "input": "x",
        "arch_review_output": large_value,
    }
    result = _compact_state_if_needed(state, "dev")
    # Should return a compaction event dict
    assert result is not None
    assert result["status"] == "progress"
    assert "state_compacted" in result["message"]
    # The state key should have been truncated
    assert len(state["arch_review_output"]) < len(large_value)


# ---------------------------------------------------------------------------
# _migrate_legacy_pm_tasks_state
# ---------------------------------------------------------------------------

def test_migrate_legacy_pm_tasks_no_op_when_new_key_present():
    state = {
        "dev_lead_output": "existing new value",
        "pm_tasks_output": "old value",
    }
    _migrate_legacy_pm_tasks_state(state)
    assert state["dev_lead_output"] == "existing new value"


def test_migrate_legacy_pm_tasks_copies_when_new_key_empty():
    state = {
        "dev_lead_output": "",
        "pm_tasks_output": "legacy value",
    }
    _migrate_legacy_pm_tasks_state(state)
    assert state["dev_lead_output"] == "legacy value"


def test_migrate_legacy_pm_tasks_no_op_when_old_key_missing():
    state = {"dev_lead_output": ""}
    _migrate_legacy_pm_tasks_state(state)
    assert state["dev_lead_output"] == ""


# ---------------------------------------------------------------------------
# human_pipeline_step_label
# ---------------------------------------------------------------------------

def test_human_step_label_dev_lead():
    assert human_pipeline_step_label("human_dev_lead") == "dev_lead"


def test_human_step_label_pm_tasks():
    assert human_pipeline_step_label("human_pm_tasks") == "dev_lead"


def test_human_step_label_code_review():
    assert human_pipeline_step_label("human_code_review") == "code_review"


def test_human_step_label_strips_prefix():
    assert human_pipeline_step_label("human_qa") == "qa"


# ---------------------------------------------------------------------------
# format_human_resume_output
# ---------------------------------------------------------------------------

def test_format_human_resume_with_feedback():
    text = format_human_resume_output("human_dev_lead", "Please fix the auth module")
    assert "[human:dev_lead]" in text
    assert "Please fix the auth module" in text


def test_format_human_resume_no_feedback():
    text = format_human_resume_output("human_qa", "")
    assert "[human:qa]" in text
    assert "Confirmed manually" in text


# ---------------------------------------------------------------------------
# append_step_feedback / get_step_retries / increment_step_retry
# ---------------------------------------------------------------------------

def test_append_step_feedback_empty_state():
    state: dict = {}
    updated = append_step_feedback(state, "dev", "issue one")
    assert updated["step_feedback"]["dev"] == ["issue one"]
    # original state not mutated
    assert state == {}


def test_append_step_feedback_accumulates():
    state: dict = {}
    s1 = append_step_feedback(state, "dev", "issue one")
    s2 = append_step_feedback(s1, "dev", "issue two")
    assert s2["step_feedback"]["dev"] == ["issue one", "issue two"]


def test_get_step_retries_zero_when_missing():
    assert get_step_retries({}, "dev") == 0


def test_get_step_retries_returns_value():
    state = {"step_retries": {"dev": 2}}
    assert get_step_retries(state, "dev") == 2


def test_increment_step_retry_from_zero():
    state: dict = {}
    updated = increment_step_retry(state, "dev")
    assert updated["step_retries"]["dev"] == 1


def test_increment_step_retry_increments():
    state = {"step_retries": {"dev": 1}}
    updated = increment_step_retry(state, "dev")
    assert updated["step_retries"]["dev"] == 2
