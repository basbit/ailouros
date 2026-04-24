from __future__ import annotations

from backend.App.integrations.infrastructure.mcp.evidence_tools import evidence_tools_definitions
from backend.App.integrations.infrastructure.mcp.openai_loop.tool_loop import MCPToolLoop


def test_local_evidence_tool_handler_without_workspace_root(monkeypatch):
    monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)

    result = MCPToolLoop._handle_local_evidence_tool("grep_context", {"query": "foo"})

    assert "SWARM_WORKSPACE_ROOT is not set" in result


def test_local_evidence_tool_handler_executes_grep_context(monkeypatch, tmp_path):
    target = tmp_path / "src" / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("alpha\nneedle\nomega\n", encoding="utf-8")
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path))

    result = MCPToolLoop._handle_local_evidence_tool("grep_context", {"query": "needle"})

    assert "src/service.py" in result
    assert "needle" in result


def test_evidence_tools_definitions_are_openai_function_tools():
    defs = evidence_tools_definitions()
    assert all(tool["type"] == "function" for tool in defs)
    assert all("function" in tool for tool in defs)
