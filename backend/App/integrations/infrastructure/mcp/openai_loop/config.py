from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPLoopConfig:
    max_rounds: int
    tool_result_max_chars: int
    max_context_chars: int
    retry_on_overflow: bool
    max_retry_count: int
    truncate_ratio: float
    history_compress_after_rounds: int
    fallback_allow: bool
    model_context_size_tokens: int
    model_context_reserve_tokens: int

    @classmethod
    def from_env(cls) -> "MCPLoopConfig":
        return cls(
            max_rounds=int(os.getenv("SWARM_MCP_MAX_ROUNDS", "5")),
            tool_result_max_chars=_mcp_tool_result_max_chars(),
            max_context_chars=_mcp_max_context_chars(),
            retry_on_overflow=_mcp_retry_on_context_overflow(),
            max_retry_count=_mcp_max_retry_count(),
            truncate_ratio=_mcp_retry_truncate_ratio(),
            history_compress_after_rounds=_mcp_history_compress_after_rounds(),
            fallback_allow=_mcp_fallback_allow(),
            model_context_size_tokens=_model_context_size_tokens(),
            model_context_reserve_tokens=_model_context_reserve_tokens(),
        )


def _mcp_tool_result_max_chars() -> int:
    env_value = os.getenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 12_000


def _mcp_fallback_allow() -> bool:
    return os.getenv("SWARM_MCP_FALLBACK_ALLOW", "0").strip().lower() in ("1", "true", "yes", "on")


def _mcp_max_context_chars() -> int:
    env_value = os.getenv("SWARM_MCP_MAX_CONTEXT_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 50_000


def _model_context_size_tokens() -> int:
    env_value = os.getenv("SWARM_MODEL_CONTEXT_SIZE", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        return parsed_int
    return 16384


def _model_context_reserve_tokens() -> int:
    env_value = os.getenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 1024


def _mcp_retry_on_context_overflow() -> bool:
    return os.getenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )


def _mcp_max_retry_count() -> int:
    env_value = os.getenv("SWARM_MCP_MAX_RETRY_COUNT", "").strip()
    if env_value.isdigit():
        return int(env_value)
    return 3


def _mcp_retry_truncate_ratio() -> float:
    env_value = os.getenv("SWARM_MCP_RETRY_TRUNCATE_RATIO", "").strip()
    if env_value:
        try:
            parsed_float = float(env_value)
            if 0.1 <= parsed_float <= 0.9:
                return parsed_float
        except ValueError:
            pass
    return 0.5


def _mcp_history_compress_after_rounds() -> int:
    env_value = os.getenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "").strip()
    if env_value.isdigit():
        return int(env_value)
    return 4
