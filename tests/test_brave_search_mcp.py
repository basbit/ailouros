"""Tests for Brave Search MCP integration."""
from __future__ import annotations

from unittest.mock import patch


def test_brave_search_mcp_config_structure():
    """brave_search_mcp_config returns a correctly structured MCP server entry."""
    from backend.App.integrations.infrastructure.mcp.web_search.brave_search_mcp import (
        brave_search_mcp_config,
    )

    cfg = brave_search_mcp_config("test-key-123")

    assert cfg["name"] == "brave_search"
    assert cfg["command"] == "npx"
    assert "-y" in cfg["args"]
    assert "@modelcontextprotocol/server-brave-search" in cfg["args"]
    assert cfg["env"]["BRAVE_API_KEY"] == "test-key-123"


def test_brave_search_mcp_config_different_key():
    """brave_search_mcp_config correctly embeds the provided API key."""
    from backend.App.integrations.infrastructure.mcp.web_search.brave_search_mcp import (
        brave_search_mcp_config,
    )

    cfg = brave_search_mcp_config("another-key-abc")
    assert cfg["env"]["BRAVE_API_KEY"] == "another-key-abc"


def test_brave_search_injected_when_env_set(monkeypatch, tmp_path):
    """When SWARM_BRAVE_SEARCH_API_KEY is set, auto adds brave_search MCP server."""
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.setenv("SWARM_BRAVE_SEARCH_API_KEY", "brave-api-key-xyz")

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch(
        "shutil.which",
        return_value="/usr/bin/npx",
    ):
        from backend.App.integrations.infrastructure.mcp.auto.auto import (
            apply_auto_mcp_to_agent_config,
        )

        result = apply_auto_mcp_to_agent_config(
            {"swarm": {}},
            workspace_root=str(workspace_dir),
        )

    servers = result.get("mcp", {}).get("servers", [])
    server_names = [s.get("name") for s in servers]
    assert "brave_search" in server_names, f"brave_search not in servers: {server_names}"

    brave_entry = next(s for s in servers if s.get("name") == "brave_search")
    assert brave_entry["env"]["BRAVE_API_KEY"] == "brave-api-key-xyz"


def test_brave_search_not_injected_when_env_absent(monkeypatch, tmp_path):
    """When SWARM_BRAVE_SEARCH_API_KEY is not set, brave_search MCP is NOT added."""
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.delenv("SWARM_BRAVE_SEARCH_API_KEY", raising=False)

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch(
        "shutil.which",
        return_value="/usr/bin/npx",
    ):
        from backend.App.integrations.infrastructure.mcp.auto.auto import (
            apply_auto_mcp_to_agent_config,
        )

        result = apply_auto_mcp_to_agent_config(
            {"swarm": {}},
            workspace_root=str(workspace_dir),
        )

    servers = result.get("mcp", {}).get("servers", [])
    server_names = [s.get("name") for s in servers]
    assert "brave_search" not in server_names, f"brave_search unexpectedly in servers: {server_names}"


def test_brave_search_not_injected_when_env_empty(monkeypatch, tmp_path):
    """When SWARM_BRAVE_SEARCH_API_KEY is empty string, brave_search MCP is NOT added."""
    monkeypatch.setenv("SWARM_MCP_AUTO", "1")
    monkeypatch.setenv("SWARM_BRAVE_SEARCH_API_KEY", "")

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    with patch(
        "backend.App.integrations.infrastructure.mcp.auto.auto._ensure_mcp_filesystem_bin",
        return_value=None,
    ), patch(
        "shutil.which",
        return_value="/usr/bin/npx",
    ):
        from backend.App.integrations.infrastructure.mcp.auto.auto import (
            apply_auto_mcp_to_agent_config,
        )

        result = apply_auto_mcp_to_agent_config(
            {"swarm": {}},
            workspace_root=str(workspace_dir),
        )

    servers = result.get("mcp", {}).get("servers", [])
    server_names = [s.get("name") for s in servers]
    assert "brave_search" not in server_names
