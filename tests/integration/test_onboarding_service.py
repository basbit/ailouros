"""Tests for backend/App/integrations/application/onboarding_service.py."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.integrations.application.onboarding_service import (
    PreconfigureResult,
    ScanResult,
    _build_proposed_config,
    _check_git,
    _check_npx,
    _context_file_exists,
    _default_mcp_recommendations,
    _detect_stack,
    _parse_preconfigure_response,
    _resolve_default_base_model,
    _sanitize_mcp_recommendations,
    _suggest_context_mode,
    _workspace_has_git_repo,
    apply_onboarding_config,
    run_ai_preconfigure,
    scan_workspace,
)


# ---------------------------------------------------------------------------
# _detect_stack
# ---------------------------------------------------------------------------

def test_detect_stack_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]")
    stacks = _detect_stack(tmp_path)
    assert "python" in stacks


def test_detect_stack_nodejs(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    stacks = _detect_stack(tmp_path)
    assert "nodejs" in stacks


def test_detect_stack_go(tmp_path):
    (tmp_path / "go.mod").write_text("module myapp")
    stacks = _detect_stack(tmp_path)
    assert "go" in stacks


def test_detect_stack_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]")
    stacks = _detect_stack(tmp_path)
    assert "rust" in stacks


def test_detect_stack_multiple(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "package.json").write_text("{}")
    stacks = _detect_stack(tmp_path)
    assert "python" in stacks
    assert "nodejs" in stacks


def test_detect_stack_empty(tmp_path):
    stacks = _detect_stack(tmp_path)
    assert stacks == []


def test_detect_stack_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n")
    stacks = _detect_stack(tmp_path)
    assert "python" in stacks


def test_detect_stack_no_duplicates(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    stacks = _detect_stack(tmp_path)
    assert stacks.count("python") == 1


# ---------------------------------------------------------------------------
# _check_npx / _check_git
# ---------------------------------------------------------------------------

def test_check_npx_found():
    with patch("shutil.which", return_value="/usr/bin/npx"):
        assert _check_npx() is True


def test_check_npx_not_found():
    with patch("shutil.which", return_value=None):
        assert _check_npx() is False


def test_check_git_found():
    with patch("shutil.which", return_value="/usr/bin/git"):
        assert _check_git() is True


def test_check_git_not_found():
    with patch("shutil.which", return_value=None):
        assert _check_git() is False


def test_workspace_has_git_repo_true(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _workspace_has_git_repo(tmp_path) is True


def test_workspace_has_git_repo_false(tmp_path):
    assert _workspace_has_git_repo(tmp_path) is False


# ---------------------------------------------------------------------------
# _context_file_exists
# ---------------------------------------------------------------------------

def test_context_file_exists_true(tmp_path):
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "context.txt").write_text("content")
    assert _context_file_exists(tmp_path) is True


def test_context_file_exists_false(tmp_path):
    assert _context_file_exists(tmp_path) is False


# ---------------------------------------------------------------------------
# _suggest_context_mode
# ---------------------------------------------------------------------------

def test_suggest_context_mode_mcp_available():
    assert _suggest_context_mode(True) == "retrieve_mcp"


def test_suggest_context_mode_no_mcp():
    assert _suggest_context_mode(False) == "retrieve_fs"


def test_default_mcp_recommendations_without_git_repo(tmp_path):
    assert _default_mcp_recommendations(tmp_path) == ["filesystem"]


def test_default_mcp_recommendations_with_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    assert _default_mcp_recommendations(tmp_path) == ["filesystem", "git"]


def test_sanitize_mcp_recommendations_drops_git_for_non_git_workspace(tmp_path):
    assert _sanitize_mcp_recommendations(["git", "filesystem"], tmp_path) == ["filesystem"]


# ---------------------------------------------------------------------------
# _build_proposed_config
# ---------------------------------------------------------------------------

def test_build_proposed_config_python(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_mcp", ["python"])
    assert "src/" in result
    assert "tests/" in result
    assert "*.py" in result


def test_build_proposed_config_nodejs(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_fs", ["nodejs"])
    assert "*.ts" in result or "*.js" in result


def test_build_proposed_config_go(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_mcp", ["go"])
    assert "cmd/" in result or "*.go" in result


def test_build_proposed_config_rust(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_mcp", ["rust"])
    assert "src/" in result


def test_build_proposed_config_unknown(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_fs", [])
    assert "unknown" in result


def test_build_proposed_config_has_header(tmp_path):
    result = _build_proposed_config(tmp_path, "retrieve_mcp", ["python"])
    assert ".swarm/context.txt" in result


# ---------------------------------------------------------------------------
# ScanResult.to_dict
# ---------------------------------------------------------------------------

def test_scan_result_to_dict():
    result = ScanResult(workspace_root="/tmp/project", detected_stack=["python"])
    d = result.to_dict()
    assert d["workspace_root"] == "/tmp/project"
    assert d["detected_stack"] == ["python"]
    assert "suggested_context_mode" in d
    assert "mcp_available" in d


# ---------------------------------------------------------------------------
# scan_workspace
# ---------------------------------------------------------------------------

def test_scan_workspace_nonexistent():
    result = scan_workspace("/nonexistent/path/12345")
    assert isinstance(result, ScanResult)
    assert result.workspace_root != ""


def test_scan_workspace_valid(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool]")
    with patch("shutil.which", return_value="/usr/bin/npx"):
        result = scan_workspace(str(tmp_path))
    assert "python" in result.detected_stack
    assert result.mcp_available is True
    assert result.suggested_context_mode == "retrieve_mcp"
    assert result.workspace_has_git_repo is False


def test_scan_workspace_marks_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    result = scan_workspace(str(tmp_path))
    assert result.workspace_has_git_repo is True


def test_scan_workspace_empty(tmp_path):
    with patch("shutil.which", return_value=None):
        result = scan_workspace(str(tmp_path))
    assert result.detected_stack == []
    assert result.mcp_available is False


def test_scan_workspace_empty_string():
    # empty string → cwd
    result = scan_workspace("")
    assert isinstance(result, ScanResult)


# ---------------------------------------------------------------------------
# apply_onboarding_config
# ---------------------------------------------------------------------------

def test_apply_onboarding_config_creates_file(tmp_path):
    apply_onboarding_config(str(tmp_path), "# context content")
    ctx = tmp_path / ".swarm" / "context.txt"
    assert ctx.exists()
    assert ctx.read_text() == "# context content"


def test_apply_onboarding_config_empty_root():
    with pytest.raises(ValueError, match="workspace_root"):
        apply_onboarding_config("", "content")


def test_apply_onboarding_config_nonexistent_root():
    with pytest.raises(ValueError, match="does not exist"):
        apply_onboarding_config("/nonexistent/path/12345", "content")


def test_apply_onboarding_config_overwrites(tmp_path):
    apply_onboarding_config(str(tmp_path), "first")
    apply_onboarding_config(str(tmp_path), "second")
    ctx = tmp_path / ".swarm" / "context.txt"
    assert ctx.read_text() == "second"


# ---------------------------------------------------------------------------
# _parse_preconfigure_response
# ---------------------------------------------------------------------------

def test_parse_preconfigure_response_plain_json():
    raw = '{"mcp_servers": ["filesystem"], "context_mode": "retrieve_mcp", "priority_paths": ["src/"]}'
    result = _parse_preconfigure_response(raw)
    assert result["mcp_servers"] == ["filesystem"]
    assert result["context_mode"] == "retrieve_mcp"


def test_parse_preconfigure_response_with_fence():
    raw = '```json\n{"mcp_servers": ["git"], "context_mode": "retrieve_fs", "priority_paths": []}\n```'
    result = _parse_preconfigure_response(raw)
    assert result["mcp_servers"] == ["git"]


def test_parse_preconfigure_response_text_before():
    raw = 'Here is my recommendation:\n{"mcp_servers": ["filesystem"], "context_mode": "retrieve_mcp", "priority_paths": []}'
    result = _parse_preconfigure_response(raw)
    assert result["mcp_servers"] == ["filesystem"]


def test_parse_preconfigure_response_invalid_raises():
    with pytest.raises(Exception):
        _parse_preconfigure_response("not json at all")


# ---------------------------------------------------------------------------
# PreconfigureResult.to_dict
# ---------------------------------------------------------------------------

def test_preconfigure_result_to_dict():
    r = PreconfigureResult(
        mcp_recommendations=["filesystem", "git"],
        context_mode="retrieve_mcp",
        priority_paths=["src/"],
        raw_response="...",
        base_model="llama3",
    )
    d = r.to_dict()
    assert d["mcp_recommendations"] == ["filesystem", "git"]
    assert d["context_mode"] == "retrieve_mcp"
    assert d["priority_paths"] == ["src/"]


# ---------------------------------------------------------------------------
# run_ai_preconfigure
# ---------------------------------------------------------------------------

def test_run_ai_preconfigure_success(tmp_path):
    mock_response = '{"mcp_servers": ["filesystem", "git"], "context_mode": "retrieve_mcp", "priority_paths": ["src/"]}'
    with patch(
        "backend.App.integrations.infrastructure.llm.client.chat_completion_text",
        return_value=mock_response,
        create=True,
    ):
        result = run_ai_preconfigure(str(tmp_path))
    assert result.error == ""
    assert "filesystem" in result.mcp_recommendations
    assert result.context_mode == "retrieve_mcp"


def test_run_ai_preconfigure_llm_fails(tmp_path):
    # Run with an environment that will fail the LLM call (no server available)
    result = run_ai_preconfigure(str(tmp_path))
    # Should always return a PreconfigureResult even when LLM fails
    assert isinstance(result, PreconfigureResult)
    # On failure, falls back to defaults
    if result.error:
        assert "filesystem" in result.mcp_recommendations


def test_run_ai_preconfigure_invalid_llm_response(tmp_path):
    with patch(
        "backend.App.integrations.infrastructure.llm.client.chat_completion_text",
        return_value="not valid json",
        create=True,
    ):
        result = run_ai_preconfigure(str(tmp_path))
    # Parse failure → fallback
    assert result.error != ""


def test_run_ai_preconfigure_custom_model(tmp_path):
    mock_response = '{"mcp_servers": ["filesystem"], "context_mode": "retrieve_mcp", "priority_paths": []}'
    with patch(
        "backend.App.integrations.infrastructure.llm.client.chat_completion_text",
        return_value=mock_response,
        create=True,
    ):
        result = run_ai_preconfigure(str(tmp_path), base_model="my-custom-model")
    assert result.base_model == "my-custom-model"


# ---------------------------------------------------------------------------
# _resolve_default_base_model
# ---------------------------------------------------------------------------

def test_resolve_default_base_model_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("SWARM_ONBOARDING_BASE_MODEL", "openai/custom")
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    assert _resolve_default_base_model() == "openai/custom"


def test_resolve_default_base_model_desktop_default(monkeypatch):
    monkeypatch.delenv("SWARM_ONBOARDING_BASE_MODEL", raising=False)
    monkeypatch.setenv("AILOUROS_DESKTOP", "1")
    assert _resolve_default_base_model() == "openai/local-default"


def test_resolve_default_base_model_web_default(monkeypatch):
    monkeypatch.delenv("SWARM_ONBOARDING_BASE_MODEL", raising=False)
    monkeypatch.delenv("AILOUROS_DESKTOP", raising=False)
    assert _resolve_default_base_model() == "lm_studio/allenai/olmo-3-32b-think"
