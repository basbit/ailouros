"""Tests for orchestrator.lifecycle_hooks (G-2)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.App.orchestration.application.pipeline.lifecycle_hooks import (
    PreflightError,
    ToolNotAllowedError,
    build_preflight_recommendations,
    build_subagent_start_event,
    run_session_preflight,
    validate_tool_use,
)


# ---------------------------------------------------------------------------
# run_session_preflight
# ---------------------------------------------------------------------------

def test_preflight_ok_with_mcp(tmp_path) -> None:
    with (
        patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x),
        patch("os.path.isdir", return_value=True),
    ):
        result = run_session_preflight(str(tmp_path), "retrieve_mcp")
    assert result["status"] == "ok"
    assert result["npx_available"] is True
    assert result["git_available"] is True


def test_preflight_fails_when_mcp_mode_no_npx(tmp_path) -> None:
    with (
        patch("shutil.which", return_value=None),
        patch("os.path.isdir", return_value=True),
    ):
        with pytest.raises(PreflightError) as exc_info:
            run_session_preflight(str(tmp_path), "retrieve_mcp")
    assert exc_info.value.code == "MCP_UNAVAILABLE"


def test_preflight_ok_retrieve_fs_no_npx(tmp_path) -> None:
    """retrieve_fs does not require npx — should not raise."""
    with (
        patch("shutil.which", return_value=None),
        patch("os.path.isdir", return_value=True),
    ):
        result = run_session_preflight(str(tmp_path), "retrieve_fs")
    assert result["status"] in ("ok", "degraded")
    assert "error" not in result or result.get("error") is None


def test_preflight_degraded_no_git(tmp_path) -> None:
    with (
        patch("shutil.which", side_effect=lambda x: "/usr/bin/npx" if x == "npx" else None),
        patch("os.path.isdir", return_value=True),
    ):
        result = run_session_preflight(str(tmp_path), "retrieve_mcp", require_git=True)
    assert result["status"] == "degraded"
    assert "git" in result.get("warning", "").lower()


def test_preflight_result_has_type() -> None:
    with (
        patch("shutil.which", return_value="/usr/bin/npx"),
        patch("os.path.isdir", return_value=True),
    ):
        result = run_session_preflight("/some/root", "retrieve_fs")
    assert result["type"] == "session_preflight"


def test_preflight_exposes_recommended_capabilities() -> None:
    with (
        patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x),
        patch("os.path.isdir", return_value=True),
        patch(
            "backend.App.integrations.infrastructure.mcp.web_search.ddg_search.ddg_search_available",
            return_value=True,
        ),
    ):
        result = run_session_preflight("/some/root", "retrieve_fs")
    names = {item["name"] for item in result["recommended_capabilities"]}
    assert "internet_search" in names
    assert "repo_evidence_tools" in names


def test_build_preflight_recommendations_marks_brave_search_configured() -> None:
    result = build_preflight_recommendations(
        "/some/root",
        "retrieve_mcp",
        mcp_config={"servers": [{"name": "brave_search", "enabled": True}]},
    )
    brave = next(item for item in result["recommended_servers"] if item["name"] == "brave_search")
    assert brave["enabled"] is True


# ---------------------------------------------------------------------------
# build_subagent_start_event
# ---------------------------------------------------------------------------

def test_subagent_start_event_fields() -> None:
    ev = build_subagent_start_event("step_1", "dev", "retrieve_mcp", tools_enabled=True)
    assert ev["type"] == "subagent_start"
    assert ev["step_id"] == "step_1"
    assert ev["agent"] == "dev"
    assert ev["context_mode"] == "retrieve_mcp"
    assert ev["tools_enabled"] is True


# ---------------------------------------------------------------------------
# validate_tool_use
# ---------------------------------------------------------------------------

def test_tool_allowed_when_no_policy() -> None:
    validate_tool_use("any_tool", None)  # should not raise


def test_tool_allowed_in_list() -> None:
    validate_tool_use("read_file", {"tools_enabled": True, "allowed_tools": ["read_file", "list_dir"]})


def test_tool_not_in_list_raises() -> None:
    with pytest.raises(ToolNotAllowedError) as exc_info:
        validate_tool_use("write_file", {"tools_enabled": True, "allowed_tools": ["read_file"]})
    assert exc_info.value.tool_name == "write_file"
    assert "read_file" in exc_info.value.allowed


def test_tools_disabled_raises() -> None:
    with pytest.raises(ToolNotAllowedError):
        validate_tool_use("any_tool", {"tools_enabled": False})


def test_empty_allowed_list_allows_all() -> None:
    validate_tool_use("any_tool", {"tools_enabled": True, "allowed_tools": []})
