"""SWARM_MCP_COMPACT_TOOLS: tool description compression in openai_tools()."""
from unittest.mock import MagicMock

from backend.App.integrations.infrastructure.mcp.stdio.mcp_pool import (
    MCPPool,
    _mcp_compact_tools_enabled,
    _mcp_tool_description_max_chars,
)


def _make_pool(tool_defs: list) -> MCPPool:
    mock_sess = MagicMock()
    mock_sess.name = "srv"
    mock_sess.list_tools.return_value = tool_defs
    pool = MCPPool.__new__(MCPPool)
    pool._sessions = [mock_sess]
    pool._cancel_event = None
    return pool


def test_compact_tools_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_COMPACT_TOOLS", raising=False)
    assert _mcp_compact_tools_enabled() is False


def test_compact_tools_enabled_via_env(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_COMPACT_TOOLS", "1")
    assert _mcp_compact_tools_enabled() is True


def test_compact_tools_enabled_true_variant(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_COMPACT_TOOLS", "true")
    assert _mcp_compact_tools_enabled() is True


def test_description_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", raising=False)
    assert _mcp_tool_description_max_chars() == 200


def test_description_max_chars_override(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", "50")
    assert _mcp_tool_description_max_chars() == 50


def test_description_max_chars_invalid_ignored(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", "abc")
    assert _mcp_tool_description_max_chars() == 200


def test_openai_tools_compact_truncates_description(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_COMPACT_TOOLS", "1")
    monkeypatch.setenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", "10")

    pool = _make_pool([{
        "name": "read_file",
        "description": "x" * 500,
        "inputSchema": {"type": "object", "properties": {}},
    }])
    tools = pool.openai_tools()

    assert len(tools) == 1
    desc = tools[0]["function"]["description"]
    # "[MCP:srv] " is 10 chars prefix + 10 chars description = 20 max
    assert len(desc) <= len("[MCP:srv] ") + 10


def test_openai_tools_no_compact_keeps_full_description(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_COMPACT_TOOLS", raising=False)

    long_description = "y" * 300
    pool = _make_pool([{
        "name": "read_file",
        "description": long_description,
        "inputSchema": {"type": "object", "properties": {}},
    }])
    tools = pool.openai_tools()

    assert long_description in tools[0]["function"]["description"]


def test_openai_tools_compact_short_description_unchanged(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_COMPACT_TOOLS", "1")
    monkeypatch.setenv("SWARM_MCP_TOOL_DESCRIPTION_MAX_CHARS", "200")

    short_description = "Read a file."
    pool = _make_pool([{
        "name": "read_file",
        "description": short_description,
        "inputSchema": {"type": "object", "properties": {}},
    }])
    tools = pool.openai_tools()

    assert short_description in tools[0]["function"]["description"]
