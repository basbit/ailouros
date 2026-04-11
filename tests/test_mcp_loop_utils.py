"""Tests for pure utility functions in mcp/openai_loop/."""
from __future__ import annotations

from backend.App.integrations.infrastructure.mcp.openai_loop.context_manager import (
    _build_tool_round_summary,
    _compress_tool_history,
    compute_user_content_budget_from_env,
    _total_messages_chars,
    _truncate_oldest_tool_results,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _mcp_fallback_allow,
    _mcp_history_compress_after_rounds,
    _mcp_max_context_chars,
    _mcp_max_retry_count,
    _mcp_retry_on_context_overflow,
    _mcp_retry_truncate_ratio,
    _mcp_tool_result_max_chars,
    _model_context_reserve_tokens,
    _model_context_size_tokens,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
    _mcp_serialize_acquire_timeout_sec,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.tool_loop import (
    _mcp_write_action_from_tool_call,
    _parse_text_tool_calls,
    _normalize_text_tool_names,
)


# ---------------------------------------------------------------------------
# _mcp_tool_result_max_chars
# ---------------------------------------------------------------------------

def test_mcp_tool_result_max_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", raising=False)
    assert _mcp_tool_result_max_chars() == 12_000


def test_mcp_tool_result_max_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", "8000")
    assert _mcp_tool_result_max_chars() == 8000


def test_mcp_tool_result_max_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", "0")
    assert _mcp_tool_result_max_chars() == 12_000


def test_mcp_tool_result_max_chars_non_digit_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", "abc")
    assert _mcp_tool_result_max_chars() == 12_000


# ---------------------------------------------------------------------------
# _mcp_fallback_allow
# ---------------------------------------------------------------------------

def test_mcp_fallback_allow_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_FALLBACK_ALLOW", raising=False)
    assert _mcp_fallback_allow() is False


def test_mcp_fallback_allow_true(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_FALLBACK_ALLOW", "1")
    assert _mcp_fallback_allow() is True


def test_mcp_fallback_allow_false_values(monkeypatch):
    for val in ("0", "false", "no"):
        monkeypatch.setenv("SWARM_MCP_FALLBACK_ALLOW", val)
        assert _mcp_fallback_allow() is False


# ---------------------------------------------------------------------------
# _mcp_max_context_chars
# ---------------------------------------------------------------------------

def test_mcp_max_context_chars_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_MAX_CONTEXT_CHARS", raising=False)
    assert _mcp_max_context_chars() == 50_000


def test_mcp_max_context_chars_custom(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_CONTEXT_CHARS", "200000")
    assert _mcp_max_context_chars() == 200_000


def test_mcp_max_context_chars_zero_uses_default(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_CONTEXT_CHARS", "0")
    assert _mcp_max_context_chars() == 50_000


# ---------------------------------------------------------------------------
# _model_context_size_tokens
# ---------------------------------------------------------------------------

def test_model_context_size_tokens_default(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_SIZE", raising=False)
    assert _model_context_size_tokens() == 16384  # safe default for modern models


def test_model_context_size_tokens_set(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    assert _model_context_size_tokens() == 4096


def test_model_context_size_tokens_zero_stays_zero(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "0")
    assert _model_context_size_tokens() == 0


# ---------------------------------------------------------------------------
# _model_context_reserve_tokens
# ---------------------------------------------------------------------------

def test_model_context_reserve_tokens_default(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", raising=False)
    assert _model_context_reserve_tokens() == 1024


def test_model_context_reserve_tokens_custom(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "1024")
    assert _model_context_reserve_tokens() == 1024


# ---------------------------------------------------------------------------
# compute_user_content_budget_from_env
# ---------------------------------------------------------------------------

def testcompute_user_content_budget_from_env_default_context(monkeypatch):
    monkeypatch.delenv("SWARM_MODEL_CONTEXT_SIZE", raising=False)
    # Default 16384 tokens → positive budget
    result = compute_user_content_budget_from_env("system prompt", [])
    assert result > 0


def testcompute_user_content_budget_from_env_with_context_size(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "512")
    result = compute_user_content_budget_from_env("sys", [])
    assert result > 0


def testcompute_user_content_budget_from_env_with_tools(monkeypatch):
    monkeypatch.setenv("SWARM_MODEL_CONTEXT_SIZE", "4096")
    tools = [
        {"function": {"description": "read a file", "parameters": {"type": "object", "properties": {}}}}
    ]
    result_no_tools = compute_user_content_budget_from_env("sys", [])
    result_with_tools = compute_user_content_budget_from_env("sys", tools)
    assert result_no_tools >= result_with_tools


# ---------------------------------------------------------------------------
# _mcp_retry_on_context_overflow
# ---------------------------------------------------------------------------

def test_mcp_retry_on_context_overflow_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", raising=False)
    assert _mcp_retry_on_context_overflow() is True


def test_mcp_retry_on_context_overflow_disabled(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "0")
    assert _mcp_retry_on_context_overflow() is False


def test_mcp_retry_on_context_overflow_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "1")
    assert _mcp_retry_on_context_overflow() is True


# ---------------------------------------------------------------------------
# _mcp_max_retry_count
# ---------------------------------------------------------------------------

def test_mcp_max_retry_count_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_MAX_RETRY_COUNT", raising=False)
    assert _mcp_max_retry_count() == 3


def test_mcp_max_retry_count_custom(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_RETRY_COUNT", "5")
    assert _mcp_max_retry_count() == 5


def test_mcp_max_retry_count_non_digit(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_MAX_RETRY_COUNT", "abc")
    assert _mcp_max_retry_count() == 3


# ---------------------------------------------------------------------------
# _mcp_retry_truncate_ratio
# ---------------------------------------------------------------------------

def test_mcp_retry_truncate_ratio_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", raising=False)
    assert _mcp_retry_truncate_ratio() == 0.5


def test_mcp_retry_truncate_ratio_valid(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.7")
    assert _mcp_retry_truncate_ratio() == 0.7


def test_mcp_retry_truncate_ratio_too_low(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.05")
    assert _mcp_retry_truncate_ratio() == 0.5  # out of range → default


def test_mcp_retry_truncate_ratio_too_high(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "0.95")
    assert _mcp_retry_truncate_ratio() == 0.5  # out of range → default


def test_mcp_retry_truncate_ratio_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "not-a-float")
    assert _mcp_retry_truncate_ratio() == 0.5


# ---------------------------------------------------------------------------
# _mcp_write_action_from_tool_call
# ---------------------------------------------------------------------------

def test_mcp_write_action_from_tool_call_write_file_create(tmp_path):
    path = tmp_path / "new.py"
    result = _mcp_write_action_from_tool_call("workspace__write_file", {"path": str(path)})
    assert result == {"path": str(path).replace("\\", "/"), "mode": "create_file"}


def test_mcp_write_action_from_tool_call_write_file_overwrite(tmp_path):
    path = tmp_path / "existing.py"
    path.write_text("x=1\n", encoding="utf-8")
    result = _mcp_write_action_from_tool_call("workspace__write_file", {"path": str(path)})
    assert result == {"path": str(path).replace("\\", "/"), "mode": "overwrite_file"}


def test_mcp_write_action_from_tool_call_edit_file_existing(tmp_path):
    path = tmp_path / "existing.py"
    path.write_text("x=1\n", encoding="utf-8")
    result = _mcp_write_action_from_tool_call("workspace__edit_file", {"path": str(path)})
    assert result == {"path": str(path).replace("\\", "/"), "mode": "patch_edit"}


# ---------------------------------------------------------------------------
# _mcp_history_compress_after_rounds
# ---------------------------------------------------------------------------

def test_mcp_history_compress_after_rounds_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", raising=False)
    assert _mcp_history_compress_after_rounds() == 4


def test_mcp_history_compress_after_rounds_custom(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "6")
    assert _mcp_history_compress_after_rounds() == 6


def test_mcp_history_compress_after_rounds_zero(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "0")
    assert _mcp_history_compress_after_rounds() == 0


# ---------------------------------------------------------------------------
# _mcp_serialize_acquire_timeout_sec
# ---------------------------------------------------------------------------

def test_mcp_serialize_acquire_timeout_sec_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", raising=False)
    assert _mcp_serialize_acquire_timeout_sec() is None


def test_mcp_serialize_acquire_timeout_sec_set(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "10.0")
    assert _mcp_serialize_acquire_timeout_sec() == 10.0


def test_mcp_serialize_acquire_timeout_sec_zero(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "0")
    assert _mcp_serialize_acquire_timeout_sec() is None


def test_mcp_serialize_acquire_timeout_sec_invalid(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_SERIALIZE_ACQUIRE_TIMEOUT_SEC", "abc")
    assert _mcp_serialize_acquire_timeout_sec() is None


# ---------------------------------------------------------------------------
# _total_messages_chars
# ---------------------------------------------------------------------------

def test_total_messages_chars_empty():
    assert _total_messages_chars([]) == 0


def test_total_messages_chars_simple():
    msgs = [{"role": "user", "content": "hello world"}]
    assert _total_messages_chars(msgs) == len("hello world")


def test_total_messages_chars_list_content():
    msgs = [{"role": "user", "content": [{"text": "part one"}, {"text": "part two"}]}]
    result = _total_messages_chars(msgs)
    assert result == len("part one") + len("part two")


def test_total_messages_chars_tool_call_arguments():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"arguments": '{"path": "/file.py"}'}}
            ],
        }
    ]
    result = _total_messages_chars(msgs)
    assert result == len('{"path": "/file.py"}')


def test_total_messages_chars_multiple_messages():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user msg"},
    ]
    result = _total_messages_chars(msgs)
    assert result == len("sys") + len("user msg")


def test_total_messages_chars_no_content():
    msgs = [{"role": "assistant"}]
    assert _total_messages_chars(msgs) == 0


# ---------------------------------------------------------------------------
# _truncate_oldest_tool_results
# ---------------------------------------------------------------------------

def test_truncate_oldest_tool_results_fits_in_budget():
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "short"},
    ]
    result = _truncate_oldest_tool_results(msgs, budget=10000)
    assert result[0]["content"] == "short"  # not truncated


def test_truncate_oldest_tool_results_truncates_oldest():
    long_content = "x" * 1000
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": long_content},
        {"role": "tool", "tool_call_id": "2", "content": long_content},
    ]
    result = _truncate_oldest_tool_results(msgs, budget=100)
    # Oldest tool result should be truncated
    assert "[truncated:" in result[0]["content"]


def test_truncate_oldest_tool_results_preserves_non_tool():
    msgs = [
        {"role": "user", "content": "x" * 1000},
        {"role": "tool", "tool_call_id": "1", "content": "y" * 1000},
    ]
    result = _truncate_oldest_tool_results(msgs, budget=100)
    # User message should not be truncated
    assert result[0]["content"] == "x" * 1000


def test_truncate_oldest_tool_results_empty():
    assert _truncate_oldest_tool_results([], budget=1000) == []


# ---------------------------------------------------------------------------
# _build_tool_round_summary
# ---------------------------------------------------------------------------

def test_build_tool_round_summary_basic():
    import json
    assistant_msg = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call1",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "/app.py"}),
                },
            }
        ],
    }
    tool_msgs = [{"role": "tool", "tool_call_id": "call1", "content": "file contents here"}]
    result = _build_tool_round_summary(assistant_msg, tool_msgs)
    assert "read_file" in result
    assert "path" in result


def test_build_tool_round_summary_no_tool_calls():
    assistant_msg = {"role": "assistant"}
    result = _build_tool_round_summary(assistant_msg, [])
    assert result == "tool call"


def test_build_tool_round_summary_invalid_args():
    assistant_msg = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "c1",
                "function": {"name": "my_tool", "arguments": "invalid json"},
            }
        ],
    }
    result = _build_tool_round_summary(assistant_msg, [])
    assert "my_tool" in result


# ---------------------------------------------------------------------------
# _compress_tool_history
# ---------------------------------------------------------------------------

def _make_tool_round(round_n: int) -> list[dict]:
    """Creates one complete tool round: assistant (with tool_calls) + tool result."""
    return [
        {
            "role": "assistant",
            "content": f"Calling tool round {round_n}",
            "tool_calls": [
                {
                    "id": f"call{round_n}",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": f'{{"path": "/file{round_n}.py"}}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": f"call{round_n}",
            "content": f"file{round_n} content",
        },
    ]


def test_compress_tool_history_below_threshold():
    msgs = (
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "do something"}]
        + _make_tool_round(1)
        + _make_tool_round(2)
    )
    result = _compress_tool_history(msgs, rounds_threshold=4)
    # Only 2 rounds, threshold is 4 → no compression
    assert result == msgs


def test_compress_tool_history_above_threshold():
    msgs = (
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "do something"}]
        + _make_tool_round(1)
        + _make_tool_round(2)
        + _make_tool_round(3)
        + _make_tool_round(4)
        + _make_tool_round(5)
    )
    result = _compress_tool_history(msgs, rounds_threshold=2)
    # Should compress first 3 rounds into a summary
    summary_msgs = [m for m in result if m.get("role") == "user" and "compressed" in m.get("content", "")]
    assert len(summary_msgs) == 1


def test_compress_tool_history_zero_threshold():
    msgs = [{"role": "system", "content": "sys"}] + _make_tool_round(1)
    result = _compress_tool_history(msgs, rounds_threshold=0)
    # threshold=0 → no compression
    assert result == msgs


def test_compress_tool_history_empty():
    assert _compress_tool_history([], rounds_threshold=4) == []


def test_compress_tool_history_preserves_order():
    msgs = (
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        + _make_tool_round(1)
        + _make_tool_round(2)
        + _make_tool_round(3)
    )
    result = _compress_tool_history(msgs, rounds_threshold=1)
    # System and user should appear at start
    assert result[0]["role"] == "system"


# ---------------------------------------------------------------------------
# _parse_text_tool_calls
# ---------------------------------------------------------------------------

def test_parse_text_tool_calls_from_reasoning_content():
    """Qwen3/DeepSeek-R1 models put tool calls in reasoning_content."""
    reasoning = (
        "Let me explore the project structure.\n\n"
        "<tool_call>\n"
        "<function=workspace_list_directory>\n"
        "<parameter=path>/Users/test/project</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    parsed = _parse_text_tool_calls(reasoning)
    assert len(parsed) == 1
    assert parsed[0].function.name == "workspace_list_directory"
    import json
    args = json.loads(parsed[0].function.arguments)
    assert args["path"] == "/Users/test/project"


def test_parse_text_tool_calls_multiple():
    """Parse multiple tool calls from single block."""
    text = (
        "<tool_call>\n"
        "<function=workspace_read_file>\n"
        "<parameter=path>/app.py</parameter>\n"
        "</function>\n"
        "</tool_call>\n"
        "<tool_call>\n"
        "<function=workspace_list_directory>\n"
        "<parameter=path>/src</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )
    parsed = _parse_text_tool_calls(text)
    assert len(parsed) == 2
    assert parsed[0].function.name == "workspace_read_file"
    assert parsed[1].function.name == "workspace_list_directory"


def test_parse_text_tool_calls_empty():
    assert _parse_text_tool_calls("") == []
    assert _parse_text_tool_calls("no tool calls here") == []


# ---------------------------------------------------------------------------
# _normalize_text_tool_names
# ---------------------------------------------------------------------------

def test_normalize_text_tool_names_single_to_double_underscore():
    """Model writes workspace_read_file → should become workspace__read_file."""
    parsed = _parse_text_tool_calls(
        "<tool_call><function=workspace_read_file>"
        "<parameter=path>/app.py</parameter>"
        "</function></tool_call>"
    )
    tools = [{"function": {"name": "workspace__read_file"}}]
    normalized = _normalize_text_tool_names(parsed, tools)
    assert normalized[0].function.name == "workspace__read_file"


def test_normalize_text_tool_names_already_correct():
    """If name already has __, leave it alone."""
    parsed = _parse_text_tool_calls(
        "<tool_call><function=workspace__read_file>"
        "<parameter=path>/a.py</parameter>"
        "</function></tool_call>"
    )
    tools = [{"function": {"name": "workspace__read_file"}}]
    normalized = _normalize_text_tool_names(parsed, tools)
    assert normalized[0].function.name == "workspace__read_file"


def test_normalize_text_tool_names_no_match_returns_as_is():
    """If no matching tool found, keep original name."""
    parsed = _parse_text_tool_calls(
        "<tool_call><function=unknown_tool>"
        "<parameter=x>1</parameter>"
        "</function></tool_call>"
    )
    tools = [{"function": {"name": "workspace__read_file"}}]
    normalized = _normalize_text_tool_names(parsed, tools)
    assert normalized[0].function.name == "unknown_tool"


def test_normalize_text_tool_names_empty_inputs():
    assert _normalize_text_tool_names([], []) == []
    assert _normalize_text_tool_names([], [{"function": {"name": "x__y"}}]) == []


# ---------------------------------------------------------------------------
# BUG-F9: gpt-oss-20b pseudo-tool-calls
# ---------------------------------------------------------------------------

def test_parse_gpt_oss_pseudo_tool_call():
    """gpt-oss-20b emits tool calls as <|start|>assistant<|channel|>... text."""
    text = (
        '<|start|>assistant<|channel|>commentary to=functions.workspace_write_file'
        '<|constrain|>json<|message|>'
        '{"path":"backend/src/App/Parser/ParserInterface.php",'
        '"content":"<?php\\ninterface ParserInterface {}"}'
    )
    parsed = _parse_text_tool_calls(text)
    assert len(parsed) == 1
    assert parsed[0].function.name == "workspace_write_file"
    import json
    args = json.loads(parsed[0].function.arguments)
    assert args["path"] == "backend/src/App/Parser/ParserInterface.php"
    assert "ParserInterface" in args["content"]


def test_parse_gpt_oss_invalid_json_skipped():
    """Invalid JSON in gpt-oss format is silently skipped."""
    text = (
        '<|start|>assistant<|channel|>commentary to=functions.workspace_write_file'
        '<|constrain|>json<|message|>{invalid json here}'
    )
    parsed = _parse_text_tool_calls(text)
    assert len(parsed) == 0


def test_parse_mixed_formats():
    """Both Qwen XML and gpt-oss formats in same text."""
    text = (
        "<tool_call><function=workspace_read_file>"
        "<parameter=path>/app.py</parameter>"
        "</function></tool_call>\n"
        '<|start|>assistant<|channel|>commentary to=functions.workspace_write_file'
        '<|constrain|>json<|message|>{"path":"test.py","content":"pass"}'
    )
    parsed = _parse_text_tool_calls(text)
    assert len(parsed) == 2
    assert parsed[0].function.name == "workspace_read_file"
    assert parsed[1].function.name == "workspace_write_file"
