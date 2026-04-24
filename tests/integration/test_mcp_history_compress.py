"""_compress_tool_history: tool-call history summarisation."""
from backend.App.integrations.infrastructure.mcp.openai_loop.context_manager import (
    _build_tool_round_summary,
    _compress_tool_history,
)
from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
    _mcp_history_compress_after_rounds,
)


def _make_round(round_num: int, tool_name: str = "read_file") -> list[dict]:
    """Return [assistant_msg, tool_msg] for one complete round."""
    call_id = f"call_{round_num}"
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": '{"path": "/src/main.py"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"round {round_num} content" * 10,
        },
    ]


def _base_messages(n_rounds: int) -> list[dict]:
    msgs: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user task"},
    ]
    for i in range(1, n_rounds + 1):
        msgs.extend(_make_round(i))
    return msgs


def test_threshold_default(monkeypatch):
    monkeypatch.delenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", raising=False)
    assert _mcp_history_compress_after_rounds() == 4


def test_threshold_override(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "2")
    assert _mcp_history_compress_after_rounds() == 2


def test_threshold_zero_disables(monkeypatch):
    monkeypatch.setenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "0")
    assert _mcp_history_compress_after_rounds() == 0


def test_no_compression_below_threshold():
    messages = _base_messages(n_rounds=3)
    result = _compress_tool_history(messages, rounds_threshold=4)
    assert result == messages


def test_no_compression_at_exact_threshold():
    messages = _base_messages(n_rounds=4)
    result = _compress_tool_history(messages, rounds_threshold=4)
    assert result == messages


def test_compression_above_threshold():
    # 5 rounds with threshold=3: rounds 1-2 → summary, rounds 3-5 kept in full
    messages = _base_messages(n_rounds=5)
    result = _compress_tool_history(messages, rounds_threshold=3)

    # system(1) + user(1) + summary_user(1) + 3 rounds * 2 msgs = 9
    assert len(result) == 2 + 1 + 3 * 2


def test_compression_summary_message_content():
    messages = _base_messages(n_rounds=5)
    result = _compress_tool_history(messages, rounds_threshold=3)

    summary_msg = result[2]
    assert summary_msg["role"] == "user"
    assert "compressed" in summary_msg["content"]
    assert "round 1" in summary_msg["content"]
    assert "round 2" in summary_msg["content"]


def test_compression_preserves_system_and_user_prefix():
    messages = _base_messages(n_rounds=5)
    result = _compress_tool_history(messages, rounds_threshold=3)

    assert result[0]["role"] == "system"
    assert result[0]["content"] == "sys"
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "user task"


def test_compression_disabled_with_zero_threshold():
    messages = _base_messages(n_rounds=10)
    result = _compress_tool_history(messages, rounds_threshold=0)
    assert result == messages


def test_compression_keeps_last_rounds_in_full():
    messages = _base_messages(n_rounds=5)
    result = _compress_tool_history(messages, rounds_threshold=3)

    # After prefix(2) + summary(1), the next 3 rounds start at index 3
    # Each round is [assistant, tool] = 2 messages
    tail = result[3:]
    assert len(tail) == 6
    # All should be assistant or tool roles
    for msg in tail:
        assert msg["role"] in ("assistant", "tool")


def test_build_tool_round_summary_basic():
    assistant_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "c1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "/foo"}'},
        }],
    }
    tool_msgs = [{"role": "tool", "tool_call_id": "c1", "content": "file content here"}]
    summary = _build_tool_round_summary(assistant_msg, tool_msgs)

    assert "read_file" in summary
    assert "chars" in summary


def test_build_tool_round_summary_empty_tool_calls():
    assistant_msg = {"role": "assistant", "content": "no tools", "tool_calls": []}
    summary = _build_tool_round_summary(assistant_msg, [])
    assert summary == "tool call"


def test_compression_many_rounds():
    # 10 rounds compressed to 2 — only 8 rounds become summary
    messages = _base_messages(n_rounds=10)
    result = _compress_tool_history(messages, rounds_threshold=2)

    # system + user + summary + 2 rounds * 2 = 7
    assert len(result) == 2 + 1 + 2 * 2
    assert "8 earlier rounds compressed" in result[2]["content"]
