"""Tests for backend/UI/REST/utils.py."""
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.UI.REST.utils import (
    _apply_retry_with,
    _cleanup_old_artifacts,
    _extract_user_prompt,
    _openai_nonstream_response,
    _pipeline_snapshot_for_disk,
    _redact_agent_config_secrets,
    _stream_incremental_workspace_enabled,
    _warn_malformed_urls,
    _workspace_followup_lines,
)


# ---------------------------------------------------------------------------
# _openai_nonstream_response
# ---------------------------------------------------------------------------

def test_openai_nonstream_response_structure():
    result = _openai_nonstream_response("hello world", "gpt-4o")
    assert result["object"] == "chat.completion"
    assert result["model"] == "gpt-4o"
    assert result["choices"][0]["message"]["content"] == "hello world"
    assert result["choices"][0]["message"]["role"] == "assistant"
    assert result["choices"][0]["finish_reason"] == "stop"


def test_openai_nonstream_response_has_id_and_created():
    result = _openai_nonstream_response("text", "m")
    assert result["id"].startswith("chatcmpl-")
    assert isinstance(result["created"], int)


# ---------------------------------------------------------------------------
# _extract_user_prompt
# ---------------------------------------------------------------------------

def test_extract_user_prompt_last_user():
    msg1 = MagicMock()
    msg1.role = "system"
    msg1.content = "system prompt"
    msg2 = MagicMock()
    msg2.role = "user"
    msg2.content = "user question"
    result = _extract_user_prompt([msg1, msg2])
    assert result == "user question"


def test_extract_user_prompt_no_user():
    msg = MagicMock()
    msg.role = "system"
    msg.content = "system"
    result = _extract_user_prompt([msg])
    assert result == "system"


def test_extract_user_prompt_empty():
    result = _extract_user_prompt([])
    assert result == ""


def test_extract_user_prompt_multiple_user_takes_last():
    m1 = MagicMock()
    m1.role = "user"
    m1.content = "first"
    m2 = MagicMock()
    m2.role = "user"
    m2.content = "last"
    result = _extract_user_prompt([m1, m2])
    assert result == "last"


# ---------------------------------------------------------------------------
# _redact_agent_config_secrets
# ---------------------------------------------------------------------------

def test_redact_agent_config_secrets_cloud():
    cfg = {"cloud": {"api_key": "secret-key", "model": "claude"}}
    result = _redact_agent_config_secrets(cfg)
    assert result["cloud"]["api_key"] == "***REDACTED***"
    assert result["cloud"]["model"] == "claude"  # preserved


def test_redact_agent_config_secrets_remote_api():
    cfg = {"remote_api": {"api_key": "sk-123", "provider": "openai"}}
    result = _redact_agent_config_secrets(cfg)
    assert result["remote_api"]["api_key"] == "***REDACTED***"
    assert result["remote_api"]["provider"] == "openai"


def test_redact_agent_config_secrets_remote_profiles():
    cfg = {
        "remote_api_profiles": {
            "fast": {"api_key": "profile-key", "provider": "openai"},
            "slow": "not-a-dict",
        }
    }
    result = _redact_agent_config_secrets(cfg)
    assert result["remote_api_profiles"]["fast"]["api_key"] == "***REDACTED***"
    assert result["remote_api_profiles"]["slow"] == "not-a-dict"


def test_redact_agent_config_secrets_no_keys():
    cfg = {"swarm": {"model": "llama3"}}
    result = _redact_agent_config_secrets(cfg)
    assert result == cfg


def test_redact_agent_config_secrets_swarm_search_keys():
    cfg = {
        "swarm": {
            "tavily_api_key": "t-key",
            "exa_api_key": "e-key",
            "scrapingdog_api_key": "s-key",
            "model": "llama3",
        }
    }
    result = _redact_agent_config_secrets(cfg)
    assert result["swarm"]["tavily_api_key"] == "***REDACTED***"
    assert result["swarm"]["exa_api_key"] == "***REDACTED***"
    assert result["swarm"]["scrapingdog_api_key"] == "***REDACTED***"
    assert result["swarm"]["model"] == "llama3"


def test_redact_agent_config_secrets_empty():
    assert _redact_agent_config_secrets(None) == {}
    assert _redact_agent_config_secrets({}) == {}


def test_redact_agent_config_secrets_no_mutation():
    cfg = {"cloud": {"api_key": "secret"}}
    original = dict(cfg)
    _redact_agent_config_secrets(cfg)
    assert cfg == original  # original not mutated


# ---------------------------------------------------------------------------
# _pipeline_snapshot_for_disk
# ---------------------------------------------------------------------------

def test_pipeline_snapshot_for_disk_redacts_agent_config():
    snap = {
        "agent_config": {"cloud": {"api_key": "secret"}},
        "task_id": "t1",
    }
    result = _pipeline_snapshot_for_disk(snap)
    assert result["agent_config"]["cloud"]["api_key"] == "***REDACTED***"
    assert result["task_id"] == "t1"


def test_pipeline_snapshot_for_disk_redacts_partial_state():
    snap = {
        "partial_state": {"agent_config": {"remote_api": {"api_key": "key123"}}},
    }
    result = _pipeline_snapshot_for_disk(snap)
    assert result["partial_state"]["agent_config"]["remote_api"]["api_key"] == "***REDACTED***"


def test_pipeline_snapshot_for_disk_no_agent_config():
    snap = {"task_id": "t1", "input": "hello"}
    result = _pipeline_snapshot_for_disk(snap)
    assert result["task_id"] == "t1"


# ---------------------------------------------------------------------------
# _stream_incremental_workspace_enabled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("1", True),
    ("true", True),
    ("0", False),
    ("false", False),
    ("off", False),
    ("", True),  # default is "1"
])
def test_stream_incremental_workspace_enabled(monkeypatch, val, expected):
    monkeypatch.setenv("SWARM_STREAM_INCREMENTAL_WORKSPACE", val)
    assert _stream_incremental_workspace_enabled() == expected


# ---------------------------------------------------------------------------
# _workspace_followup_lines
# ---------------------------------------------------------------------------

def test_workspace_followup_lines_no_path():
    lines = _workspace_followup_lines(None, True, {})
    assert len(lines) == 1
    assert "not set" in lines[0]


def test_workspace_followup_lines_no_write():
    lines = _workspace_followup_lines(Path("/tmp"), False, {})
    assert len(lines) == 1
    assert "workspace_write=false" in lines[0]


def test_workspace_followup_lines_write_not_allowed(monkeypatch):
    monkeypatch.delenv("SWARM_ALLOW_WORKSPACE_WRITE", raising=False)
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.workspace_write_allowed",
        return_value=False,
    ):
        lines = _workspace_followup_lines(Path("/tmp"), True, {})
    assert "SWARM_ALLOW_WORKSPACE_WRITE" in lines[0]


def test_workspace_followup_lines_writes_ok(monkeypatch):
    snap = {
        "workspace_writes": {"written": ["a.py", "b.py"], "errors": [], "note": "ok"},
    }
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.any_snapshot_output_has_swarm",
        return_value=True,
    ):
        lines = _workspace_followup_lines(Path("/tmp"), True, snap)
    assert "files_written=2" in lines[0]
    assert "errors=0" in lines[0]


def test_workspace_followup_lines_with_errors(monkeypatch):
    snap = {
        "workspace_writes": {"written": [], "errors": ["err1", "err2"], "note": ""},
    }
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.any_snapshot_output_has_swarm",
        return_value=True,
    ):
        lines = _workspace_followup_lines(Path("/tmp"), True, snap)
    error_lines = [line for line in lines if "write errors" in line]
    assert len(error_lines) == 1


def test_workspace_followup_lines_hint_no_swarm_tags(monkeypatch):
    snap = {}
    with patch(
        "backend.App.workspace.infrastructure.workspace_io.workspace_write_allowed",
        return_value=True,
    ), patch(
        "backend.App.workspace.infrastructure.patch_parser.any_snapshot_output_has_swarm",
        return_value=False,
    ):
        lines = _workspace_followup_lines(Path("/tmp"), True, snap)
    hint_lines = [line for line in lines if "swarm_file" in line]
    assert len(hint_lines) == 1


# ---------------------------------------------------------------------------
# _cleanup_old_artifacts
# ---------------------------------------------------------------------------

def test_cleanup_old_artifacts_removes_old(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACT_TTL_DAYS", "1")
    old_dir = tmp_path / "old-task-123"
    old_dir.mkdir()
    # Set mtime to 2 days ago
    old_time = time.time() - 2 * 86400
    import os
    os.utime(old_dir, (old_time, old_time))
    asyncio.run(_cleanup_old_artifacts(tmp_path))
    assert not old_dir.exists()


def test_cleanup_old_artifacts_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACT_TTL_DAYS", "7")
    recent_dir = tmp_path / "recent-task"
    recent_dir.mkdir()
    asyncio.run(_cleanup_old_artifacts(tmp_path))
    assert recent_dir.exists()


def test_cleanup_old_artifacts_ttl_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_ARTIFACT_TTL_DAYS", "0")
    d = tmp_path / "task"
    d.mkdir()
    asyncio.run(_cleanup_old_artifacts(tmp_path))
    assert d.exists()


# ---------------------------------------------------------------------------
# _warn_malformed_urls
# ---------------------------------------------------------------------------

def test_warn_malformed_urls_no_vars(monkeypatch):
    for var in ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "LMSTUDIO_BASE_URL", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    _warn_malformed_urls()  # should not raise


def test_warn_malformed_urls_malformed(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_BASE_URL", "not-a-url")
    import logging
    with caplog.at_level(logging.WARNING):
        _warn_malformed_urls()
    assert any("malformed" in r.message or "OPENAI_BASE_URL" in r.message for r in caplog.records)


def test_warn_malformed_urls_valid(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    _warn_malformed_urls()  # should not warn


# ---------------------------------------------------------------------------
# _apply_retry_with
# ---------------------------------------------------------------------------

def test_apply_retry_with_different_model():
    ac = {"dev": {"model": "old-model"}, "pm": {"model": "old-pm"}}
    retry = MagicMock()
    retry.different_model = "new-model"
    retry.tools_off = False
    retry.reduced_context = None
    result = _apply_retry_with(ac, {}, retry)
    assert result["dev"]["model"] == "new-model"
    assert result["pm"]["model"] == "new-model"


def test_apply_retry_with_tools_off():
    ac = {"dev": {"mcp": {"servers": ["srv1"]}}}
    retry = MagicMock()
    retry.different_model = None
    retry.tools_off = True
    retry.reduced_context = None
    result = _apply_retry_with(ac, {}, retry)
    assert result["dev"]["mcp"]["servers"] == []


def test_apply_retry_with_reduced_context():
    ac = {}
    state = {}
    retry = MagicMock()
    retry.different_model = None
    retry.tools_off = False
    retry.reduced_context = "index_only"
    _apply_retry_with(ac, state, retry)
    assert state["workspace_context_mode"] == "index_only"


def test_apply_retry_with_no_changes():
    ac = {"dev": {"model": "llama3"}}
    retry = MagicMock()
    retry.different_model = None
    retry.tools_off = False
    retry.reduced_context = None
    result = _apply_retry_with(ac, {}, retry)
    assert result["dev"]["model"] == "llama3"
