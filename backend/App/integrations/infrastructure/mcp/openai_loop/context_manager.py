"""Context-window management helpers for the OpenAI-compat MCP tool loop.

Contains:
- _total_messages_chars
- _truncate_oldest_tool_results
- _build_tool_round_summary
- _compress_tool_history
- _compute_user_content_budget
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _total_messages_chars(messages: list[dict]) -> int:
    """Rough character count across all message content fields."""
    total = 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += len(str(part.get("text") or ""))
        # tool_calls list — count arguments strings
        for tool_call in message.get("tool_calls") or []:
            if isinstance(tool_call, dict):
                function_data = tool_call.get("function") or {}
                total += len(function_data.get("arguments") or "")
    return total


def _truncate_oldest_tool_results(messages: list[dict], budget: int) -> list[dict]:
    """Truncate content of the oldest `tool` role messages until total fits in budget.

    Only `tool` role messages (results) are modified.  `assistant` messages that
    carry `tool_calls` entries are left intact so the conversation structure remains
    valid for the LLM.
    """
    result = list(messages)
    for idx, msg in enumerate(result):
        if _total_messages_chars(result) <= budget:
            break
        if msg.get("role") != "tool":
            continue
        original_content = msg.get("content") or ""
        if not isinstance(original_content, str):
            continue
        orig_len = len(original_content)
        if orig_len == 0:
            continue
        truncation_notice = f"[truncated: original {orig_len} chars]"
        result[idx] = {**msg, "content": truncation_notice}
        logger.warning(
            "MCP context guard: truncated tool result (tool_call_id=%r) from %d to %d chars "
            "(total budget SWARM_MCP_MAX_CONTEXT_CHARS=%d)",
            msg.get("tool_call_id") or "",
            orig_len,
            len(truncation_notice),
            budget,
        )
    return result


def _build_tool_round_summary(assistant_msg: dict, tool_msgs: list[dict]) -> str:
    """Build a one-line summary for a single tool-call round.

    Format: tool_name(first_arg=val→N chars), …
    """
    parts: list[str] = []
    tool_calls = assistant_msg.get("tool_calls") or []
    tool_results: dict[str, str] = {
        tm.get("tool_call_id", ""): str(tm.get("content") or "") for tm in tool_msgs
    }
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function_data = tool_call.get("function") or {}
        tool_name = function_data.get("name") or "tool"
        try:
            import json as _json
            raw_args = _json.loads(function_data.get("arguments") or "{}")
            if isinstance(raw_args, dict) and raw_args:
                first_key = next(iter(raw_args))
                first_val = str(raw_args[first_key])[:40]
                arg_summary = f"{first_key}={first_val!r}"
            else:
                arg_summary = ""
        except Exception:
            arg_summary = ""
        result_text = tool_results.get(tool_call.get("id") or "", "")
        parts.append(f"{tool_name}({arg_summary}→{len(result_text)} chars)")
    return ", ".join(parts) if parts else "tool call"


def _compress_tool_history(messages: list[dict], rounds_threshold: int) -> list[dict]:
    """Replace all but the last rounds_threshold tool-call rounds with a summary message.

    Algorithm:
    1. Split messages into prefix (system + user) and completed tool-call rounds
       (each round = one assistant-with-tool_calls + its tool result messages).
    2. If completed rounds <= rounds_threshold, return unchanged.
    3. Summarise the earliest (n - rounds_threshold) rounds into one user message.
    4. Return: prefix + [summary_user] + last rounds_threshold rounds in full.

    Invariant: the original system and user messages are always preserved.
    """
    if rounds_threshold <= 0:
        return list(messages)

    prefix: list[dict] = []
    rounds: list[list[dict]] = []
    current_round: list[dict] = []

    for msg in messages:
        role = msg.get("role")
        if role in ("system", "user") and not rounds and not current_round:
            prefix.append(msg)
            continue
        if role == "assistant" and msg.get("tool_calls"):
            if current_round:
                rounds.append(current_round)
            current_round = [msg]
        elif role == "tool" and current_round:
            current_round.append(msg)
        else:
            if current_round:
                rounds.append(current_round)
                current_round = []
            prefix.append(msg)

    if current_round:
        rounds.append(current_round)

    if len(rounds) <= rounds_threshold:
        return list(messages)

    compress_rounds = rounds[:-rounds_threshold]
    keep_rounds = rounds[-rounds_threshold:]

    summary_parts: list[str] = []
    for round_index, round_msgs in enumerate(compress_rounds):
        if not round_msgs:
            continue
        assistant_msg = round_msgs[0]
        tool_msgs = round_msgs[1:]
        summary = _build_tool_round_summary(assistant_msg, tool_msgs)
        summary_parts.append(f"round {round_index + 1}: {summary}")

    compressed_message_count = sum(len(r) for r in compress_rounds)
    summary_text = (
        "[tool history summary — "
        + str(len(compress_rounds))
        + " earlier rounds compressed]\n"
        + "\n".join(summary_parts)
    )
    logger.info(
        "MCP history compress: %d rounds → 1 summary message (%d messages removed, %d rounds kept detailed)",
        len(compress_rounds), compressed_message_count, len(keep_rounds),
    )
    result = list(prefix) + [{"role": "user", "content": summary_text}]
    for round_msgs in keep_rounds:
        result.extend(round_msgs)
    return result


def _compute_user_content_budget(
    system_prompt: str,
    tools: list[dict],
    *,
    model_context_size_tokens: int = 0,
    model_context_reserve_tokens: int = 512,
) -> int:
    """Compute the maximum allowed character length of user_content.

    Returns 0 when model_context_size_tokens is 0 (no budget enforcement).

    Token estimation: 1 token ≈ 3 chars (mixed English/code approximation).
    Tool schemas are estimated from their serialised description + parameters lengths.
    """
    context_tokens = model_context_size_tokens
    if not context_tokens:
        return 0
    reserve_tokens = model_context_reserve_tokens
    chars_per_token = 3
    tool_schemas_chars = sum(
        len(str(t.get("function", {}).get("description") or ""))
        + len(str(t.get("function", {}).get("parameters") or ""))
        for t in tools
    )
    available_tokens = (
        context_tokens
        - reserve_tokens
        - len(system_prompt) // chars_per_token
        - tool_schemas_chars // chars_per_token
    )
    if available_tokens <= 0:
        return 0
    return available_tokens * chars_per_token


def compute_user_content_budget_from_env(
    system_prompt: str,
    tools: list[dict[str, Any]],
) -> int:
    """Compute user_content budget reading context size from environment.

    Thin wrapper that reads env vars and delegates to
    :func:`_compute_user_content_budget`.  Kept for backwards compatibility
    with the call site in loop.py.
    """
    from backend.App.integrations.infrastructure.mcp.openai_loop.config import (
        _model_context_size_tokens,
        _model_context_reserve_tokens,
    )
    return _compute_user_content_budget(
        system_prompt,
        tools,
        model_context_size_tokens=_model_context_size_tokens(),
        model_context_reserve_tokens=_model_context_reserve_tokens(),
    )
