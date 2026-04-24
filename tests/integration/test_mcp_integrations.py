"""Tests for MCP integration modules (P0/P1 backlog).

Tests cover:
- fetch_page: tool definition, HTML->text, error handling
- git_mcp: config generation, workspace detection
- context7_mcp: config generation
- github_mcp: config generation, token detection
- auto.py: injection of new servers/tools
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from backend.App.integrations.infrastructure.mcp.web_search.fetch_page import (
    _html_to_text,
    fetch_page,
    fetch_page_available,
    fetch_page_tool_definition,
)
from backend.App.integrations.infrastructure.mcp.git.git_mcp import (
    git_mcp_config,
    workspace_has_git,
)
from backend.App.integrations.infrastructure.mcp.docs.context7_mcp import (
    context7_mcp_config,
)
from backend.App.integrations.infrastructure.mcp.github.github_mcp import (
    github_mcp_config,
    github_token,
)
from backend.App.integrations.infrastructure.mcp.auto.auto import (
    apply_auto_mcp_to_agent_config,
)


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


def test_fetch_page_available():
    assert fetch_page_available() is True  # httpx is in requirements


def test_fetch_page_tool_definition_shape():
    td = fetch_page_tool_definition()
    assert td["type"] == "function"
    fn = td["function"]
    assert fn["name"] == "fetch_page"
    assert "url" in fn["parameters"]["properties"]
    assert "url" in fn["parameters"]["required"]


def test_html_to_text_basic():
    html = "<html><body><h1>Title</h1><p>Hello <b>world</b></p></body></html>"
    text = _html_to_text(html)
    assert "Title" in text
    assert "Hello world" in text


def test_html_to_text_strips_scripts():
    html = "<p>Before</p><script>alert(1)</script><p>After</p>"
    text = _html_to_text(html)
    assert "Before" in text
    assert "After" in text
    assert "alert" not in text


def test_fetch_page_empty_url():
    result = fetch_page("")
    assert "ERROR" in result


def test_fetch_page_invalid_scheme():
    result = fetch_page("ftp://example.com")
    assert "ERROR" in result
    assert "HTTP/HTTPS" in result


def test_fetch_page_bad_url():
    """Non-existent domain should return error, not raise."""
    result = fetch_page("http://this-domain-does-not-exist-12345.test/page")
    assert "ERROR" in result


# ---------------------------------------------------------------------------
# git_mcp
# ---------------------------------------------------------------------------


def test_git_mcp_config_shape():
    cfg = git_mcp_config("/tmp/myrepo")
    assert cfg["name"] == "git"
    assert cfg["command"] == "uvx"
    args_str = " ".join(cfg.get("args", []))
    assert "mcp-server-git" in args_str
    assert "/tmp/myrepo" in args_str


def test_workspace_has_git_true(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    assert workspace_has_git(str(tmp_path)) is True


def test_workspace_has_git_false(tmp_path: Path):
    assert workspace_has_git(str(tmp_path)) is False


def test_workspace_has_git_empty():
    assert workspace_has_git("") is False


# ---------------------------------------------------------------------------
# context7_mcp
# ---------------------------------------------------------------------------


def test_context7_config_shape():
    cfg = context7_mcp_config()
    assert cfg["name"] == "context7"
    assert "context7-mcp" in " ".join(cfg.get("args", []))


# ---------------------------------------------------------------------------
# github_mcp
# ---------------------------------------------------------------------------


def test_github_config_shape():
    cfg = github_mcp_config("ghp_test123")
    assert cfg["name"] == "github"
    assert cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_test123"


def test_github_token_from_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    assert github_token() == "ghp_abc"


def test_github_token_from_pat(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_xyz")
    assert github_token() == "ghp_xyz"


def test_github_token_empty(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    assert github_token() == ""


# ---------------------------------------------------------------------------
# auto.py integration: new servers injected
# ---------------------------------------------------------------------------


def _server_names(ac: dict) -> list[str]:
    return [s["name"] for s in ac.get("mcp", {}).get("servers", [])]


def _auto_with_clean_env(agent_config: dict, workspace_root: str, **env_overrides) -> dict:
    """Call apply_auto_mcp_to_agent_config with real-config isolation."""
    old_cfg = os.environ.pop("SWARM_MCP_CONFIG", None)
    old_auto = os.environ.get("SWARM_MCP_AUTO")
    os.environ["SWARM_MCP_AUTO"] = "1"  # conftest sets it to "0"
    try:
        with patch(
            "backend.App.integrations.infrastructure.mcp.auto.auto._load_mcp_config_file",
            return_value=None,
        ):
            for k, v in env_overrides.items():
                os.environ[k] = v
            try:
                return apply_auto_mcp_to_agent_config(
                    agent_config, workspace_root=workspace_root,
                )
            finally:
                for k in env_overrides:
                    os.environ.pop(k, None)
    finally:
        if old_cfg is not None:
            os.environ["SWARM_MCP_CONFIG"] = old_cfg
        if old_auto is not None:
            os.environ["SWARM_MCP_AUTO"] = old_auto
        else:
            os.environ.pop("SWARM_MCP_AUTO", None)


def test_auto_injects_git_when_workspace_has_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    with patch(
        "backend.App.integrations.infrastructure.mcp.git.git_mcp.git_mcp_available",
        return_value=True,
    ):
        ac = _auto_with_clean_env({"swarm": {"git_mcp": True}}, str(tmp_path))
    assert "git" in _server_names(ac)


def test_auto_skips_git_when_no_dotgit(tmp_path: Path):
    ac = _auto_with_clean_env({"swarm": {"git_mcp": True}}, str(tmp_path))
    assert "git" not in _server_names(ac)


def test_auto_injects_context7_when_flag_set(tmp_path: Path):
    ac = _auto_with_clean_env({"swarm": {"context7": True}}, str(tmp_path))
    assert "context7" in _server_names(ac)


def test_auto_no_context7_by_default(tmp_path: Path):
    ac = _auto_with_clean_env({}, str(tmp_path))
    assert "context7" not in _server_names(ac)


def test_auto_injects_github_when_token_set(tmp_path: Path):
    ac = _auto_with_clean_env(
        {"swarm": {"github_mcp": True}}, str(tmp_path),
        GITHUB_TOKEN="ghp_test_auto",
    )
    assert "github" in _server_names(ac)


def test_auto_no_github_without_token(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    ac = _auto_with_clean_env({"swarm": {"github_mcp": True}}, str(tmp_path))
    assert "github" not in _server_names(ac)
