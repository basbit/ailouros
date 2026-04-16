"""MCPLoopConfig — all env-driven knobs for the OpenAI-compat MCP tool loop."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MCPLoopConfig:
    """Immutable configuration for :func:`run_with_mcp_tools_openai_compat`.

    Build from environment variables via :meth:`from_env`.
    """

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
        """Read all knobs from environment variables and return a frozen config."""
        return cls(
            # Default 5 rounds (was 8). On a local reasoning model with a
            # 20 KB prompt, each tool round re-prefills the accumulating
            # conversation (~3–6 s/round prefill). Rounds 6–8 typically just
            # repeat previous tool calls; the final format-enforcement retry
            # at the end of the subtask catches the rare legitimate case.
            # Operators can still bump SWARM_MCP_MAX_ROUNDS for complex
            # cloud workflows where prefill is effectively free.
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


# ---------------------------------------------------------------------------
# Individual env-reader helpers (kept here for backwards-compat imports)
# ---------------------------------------------------------------------------

def _mcp_tool_result_max_chars() -> int:
    """Per-tool-result truncation limit.

    Default 12_000 chars — fits within a 16K-token model budget alongside
    system prompt + tools schema + user content. For large-context cloud models
    increase: SWARM_MCP_TOOL_RESULT_MAX_CHARS=120000.
    """
    env_value = os.getenv("SWARM_MCP_TOOL_RESULT_MAX_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 12_000


def _mcp_fallback_allow() -> bool:
    """Разрешить продолжение без MCP-инструментов при сбое.

    По умолчанию false — сбой MCP = явная ошибка шага.
    Включить через SWARM_MCP_FALLBACK_ALLOW=1 только осознанно.
    """
    return os.getenv("SWARM_MCP_FALLBACK_ALLOW", "0").strip().lower() in ("1", "true", "yes", "on")


def _mcp_max_context_chars() -> int:
    """Total character budget for the full messages[] list sent to the LLM.

    Prevents runaway accumulation across many tool-call rounds.
    Default 50_000 chars (~12K tokens) — fits local models with 16K context.
    For cloud models: SWARM_MCP_MAX_CONTEXT_CHARS=400000.
    """
    env_value = os.getenv("SWARM_MCP_MAX_CONTEXT_CHARS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 50_000


def _model_context_size_tokens() -> int:
    """Model context window size in tokens.

    Default 16384 — safe for all modern local models (>=8K context).
    Override: SWARM_MODEL_CONTEXT_SIZE=32768 if model is loaded with larger context.
    Set to 0 to disable budget enforcement (risky — may cause 400 errors).

    IMPORTANT: This should match the actual n_ctx in LM Studio / Ollama settings.
    If your model supports 32K but LM Studio loads it with 4096 (default),
    set this to 4096 or increase n_ctx in LM Studio.
    """
    env_value = os.getenv("SWARM_MODEL_CONTEXT_SIZE", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        return parsed_int  # 0 = explicitly disabled
    return 16384  # safe default for modern local models


def _model_context_reserve_tokens() -> int:
    """Token reserve for model response generation.

    Default 1024 — headroom for the model's response text.
    System prompt and tools are budgeted separately.
    """
    env_value = os.getenv("SWARM_MODEL_CONTEXT_RESERVE_TOKENS", "").strip()
    if env_value.isdigit():
        parsed_int = int(env_value)
        if parsed_int > 0:
            return parsed_int
    return 1024


def _mcp_retry_on_context_overflow() -> bool:
    """Auto-retry on context-overflow error by halving user_content (enabled by default).

    On context-overflow 400 errors the user message is truncated by
    SWARM_MCP_RETRY_TRUNCATE_RATIO and the round is retried up to
    SWARM_MCP_MAX_RETRY_COUNT times.  Disable via SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW=0.
    """
    return os.getenv("SWARM_MCP_RETRY_ON_CONTEXT_OVERFLOW", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )


def _mcp_max_retry_count() -> int:
    """Maximum number of auto-retry attempts on context overflow (default 3).

    Each retry truncates user_content by SWARM_MCP_RETRY_TRUNCATE_RATIO.
    Override via SWARM_MCP_MAX_RETRY_COUNT env var.
    """
    env_value = os.getenv("SWARM_MCP_MAX_RETRY_COUNT", "").strip()
    if env_value.isdigit():
        return int(env_value)
    return 3


def _mcp_retry_truncate_ratio() -> float:
    """Fraction of current user_content kept on auto-retry (default 0.5).

    Values outside [0.1, 0.9] are silently ignored and the default is used.
    """
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
    """Number of tool-call rounds to keep in full detail; earlier rounds are summarised.

    When the number of completed rounds exceeds this threshold, all but the last
    N rounds are replaced by a single compact summary user message.
    Set to 0 to disable compression entirely.
    Default 4: compression starts after the 4th completed round.
    """
    env_value = os.getenv("SWARM_MCP_HISTORY_COMPRESS_AFTER_ROUNDS", "").strip()
    if env_value.isdigit():
        return int(env_value)  # 0 is valid (disables compression)
    return 4
