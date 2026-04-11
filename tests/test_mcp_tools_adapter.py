"""Tests for backend/App/integrations/infrastructure/mcp_tools_adapter.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from backend.App.integrations.infrastructure.mcp_tools_adapter import McpToolsAdapter
from backend.App.orchestration.domain.ports import ToolResult, ToolSchema


def _make_pool_mock(tools=None, call_result=None):
    """Return a mock MCPPool context manager."""
    pool = MagicMock()
    pool.openai_tools.return_value = tools or []
    pool.call_tool.return_value = call_result if call_result is not None else "result text"
    pool.__enter__ = MagicMock(return_value=pool)
    pool.__exit__ = MagicMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------

def test_list_tools_empty():
    adapter = McpToolsAdapter(mcp_servers=[], workspace_root="/tmp")

    pool = _make_pool_mock(tools=[])
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.list_tools()

    assert result == []


def test_list_tools_maps_schemas():
    adapter = McpToolsAdapter(mcp_servers=[{"name": "fs"}], workspace_root="/tmp")

    raw_tools = [
        {
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            }
        },
        {
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {"type": "object"},
            }
        },
    ]
    pool = _make_pool_mock(tools=raw_tools)
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.list_tools()

    assert len(result) == 2
    assert isinstance(result[0], ToolSchema)
    assert result[0].name == "read_file"
    assert result[0].description == "Read a file"
    assert result[1].name == "write_file"


def test_list_tools_missing_function_key():
    """Tool without 'function' key → empty name/description."""
    adapter = McpToolsAdapter(mcp_servers=[])

    raw_tools = [{"not_function": {}}]
    pool = _make_pool_mock(tools=raw_tools)
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.list_tools()

    assert len(result) == 1
    assert result[0].name == ""
    assert result[0].description == ""


def test_list_tools_initializes_with_servers():
    """MCPPool is called with the correct servers (workspace_root is NOT passed to MCPPool)."""
    adapter = McpToolsAdapter(mcp_servers=[{"name": "test"}], workspace_root="/work")

    pool = _make_pool_mock(tools=[])
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ) as mock_pool_cls:
        adapter.list_tools()

    mock_pool_cls.assert_called_once_with([{"name": "test"}])


# ---------------------------------------------------------------------------
# call_tool
# ---------------------------------------------------------------------------

def test_call_tool_string_result():
    adapter = McpToolsAdapter(mcp_servers=[])

    pool = _make_pool_mock(call_result="tool output here")
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.call_tool("read_file", {"path": "/tmp/test.py"})

    assert isinstance(result, ToolResult)
    assert result.content == "tool output here"
    assert result.is_error is False


def test_call_tool_dict_result_not_error():
    adapter = McpToolsAdapter(mcp_servers=[])

    pool = _make_pool_mock(call_result={"content": "file data", "isError": False})
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.call_tool("read_file", {})

    assert result.is_error is False


def test_call_tool_dict_result_is_error():
    adapter = McpToolsAdapter(mcp_servers=[])

    pool = _make_pool_mock(call_result={"error": "File not found", "isError": True})
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.call_tool("read_file", {"path": "/nonexistent"})

    assert result.is_error is True


def test_call_tool_non_string_non_dict_converted():
    adapter = McpToolsAdapter(mcp_servers=[])

    pool = _make_pool_mock(call_result=42)  # Neither str nor dict
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        result = adapter.call_tool("some_tool", {})

    assert result.content == "42"
    assert result.is_error is False


def test_call_tool_passes_name_and_args():
    adapter = McpToolsAdapter(mcp_servers=[])

    pool = _make_pool_mock(call_result="ok")
    with patch(
        "backend.App.integrations.infrastructure.mcp.stdio.session.MCPPool",
        return_value=pool,
        create=True,
    ):
        adapter.call_tool("my_tool", {"key": "value"})

    pool.call_tool.assert_called_once_with("my_tool", {"key": "value"})
