"""SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW: auto-retry on context overflow."""
from unittest.mock import MagicMock, patch

import pytest

from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _mcp_max_retry_count,
    _mcp_retry_on_context_overflow,
    _mcp_retry_truncate_ratio,
)


def test_retry_enabled_by_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", raising=False)
    assert _mcp_retry_on_context_overflow() is True


def test_retry_disabled_via_zero(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "0")
    assert _mcp_retry_on_context_overflow() is False


def test_retry_disabled_via_false(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "false")
    assert _mcp_retry_on_context_overflow() is False


def test_truncate_ratio_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", raising=False)
    assert _mcp_retry_truncate_ratio() == 0.5


def test_truncate_ratio_valid(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.3")
    assert _mcp_retry_truncate_ratio() == pytest.approx(0.3)


def test_truncate_ratio_too_small_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.05")
    assert _mcp_retry_truncate_ratio() == 0.5


def test_truncate_ratio_too_large_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.95")
    assert _mcp_retry_truncate_ratio() == 0.5


def test_truncate_ratio_invalid_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "notafloat")
    assert _mcp_retry_truncate_ratio() == 0.5


def test_max_retry_count_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_MAX_RETRY_COUNT", raising=False)
    assert _mcp_max_retry_count() == 3


def test_max_retry_count_override(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_RETRY_COUNT", "5")
    assert _mcp_max_retry_count() == 5


def test_max_retry_count_zero_disables_retries(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_RETRY_COUNT", "0")
    assert _mcp_max_retry_count() == 0


def _make_mock_pool(tool_list=None):
    mock_pool = MagicMock()
    mock_pool.__enter__ = MagicMock(return_value=mock_pool)
    mock_pool.__exit__ = MagicMock(return_value=False)
    mock_pool.openai_tools.return_value = tool_list or [
        {"type": "function", "function": {"name": "t", "description": "", "parameters": {}}}
    ]
    return mock_pool


def _patch_mcp(mock_client, mock_pool):
    return [
        patch(
            "backend.App.integrations.infrastructure.mcp.openai_loop.loop._build_openai_client_for_env",
            return_value=(mock_client, "local:ollama"),
        ),
        patch(
            "backend.App.integrations.infrastructure.mcp.openai_loop.loop.MCPPool",
            return_value=mock_pool,
        ),
        patch(
            "backend.App.integrations.infrastructure.mcp.openai_loop.loop.load_mcp_server_defs",
            return_value=[{"name": "s", "command": ["sh"]}],
        ),
    ]


def test_retries_once_on_context_overflow(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "1")
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.5")

    overflow_exc = Exception("tokens to keep from initial prompt is greater than context length")
    success_resp = MagicMock()
    success_resp.choices = [MagicMock()]
    success_resp.choices[0].message.content = "ok"
    success_resp.choices[0].message.tool_calls = []

    call_count = {"n": 0}

    def side_effect(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise overflow_exc
        return success_resp

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = side_effect
    mock_client.base_url = "http://localhost:11434"

    mock_pool = _make_mock_pool()
    patches = _patch_mcp(mock_client, mock_pool)

    from backend.App.integrations.infrastructure.mcp.openai_loop.loop import run_with_mcp_tools_openai_compat

    with patches[0], patches[1], patches[2]:
        result_text, _, _ = run_with_mcp_tools_openai_compat(
            system_prompt="sys",
            user_content="u" * 1000,
            model="phi-4",
            mcp_cfg={"servers": [{"name": "s", "command": "sh"}]},
        )

    assert call_count["n"] == 2
    assert result_text == "ok"


def test_exhausts_all_retries_on_repeated_overflow(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "1")
    monkeypatch.setenv("SWARM_MCP_MAX_RETRY_COUNT", "3")
    overflow_exc = Exception("tokens to keep from initial prompt is greater than context length")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = overflow_exc
    mock_client.base_url = "http://localhost:11434"

    mock_pool = _make_mock_pool()
    patches = _patch_mcp(mock_client, mock_pool)

    from backend.App.integrations.infrastructure.mcp.openai_loop.loop import run_with_mcp_tools_openai_compat

    with patches[0], patches[1], patches[2]:
        with pytest.raises(Exception, match="tokens to keep"):
            run_with_mcp_tools_openai_compat(
                system_prompt="sys",
                user_content="u" * 1000,
                model="phi-4",
                mcp_cfg={"servers": [{"name": "s", "command": "sh"}]},
            )

    # Original call + 3 retries = 4 total
    assert mock_client.chat.completions.create.call_count == 4


def test_retry_disabled_raises_immediately(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "0")
    overflow_exc = Exception("tokens to keep from initial prompt is greater than context length")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = overflow_exc
    mock_client.base_url = "http://localhost:11434"

    mock_pool = _make_mock_pool()
    patches = _patch_mcp(mock_client, mock_pool)

    from backend.App.integrations.infrastructure.mcp.openai_loop.loop import run_with_mcp_tools_openai_compat

    with patches[0], patches[1], patches[2]:
        with pytest.raises(Exception, match="tokens to keep"):
            run_with_mcp_tools_openai_compat(
                system_prompt="sys",
                user_content="u" * 1000,
                model="phi-4",
                mcp_cfg={"servers": [{"name": "s", "command": "sh"}]},
            )

    # No retry — only 1 call
    assert mock_client.chat.completions.create.call_count == 1
