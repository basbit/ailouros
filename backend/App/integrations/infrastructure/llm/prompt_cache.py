"""M-8 — Prompt fragment caching.

Marks stable parts of Anthropic API calls with ``cache_control`` so the
provider can serve them from its prompt-cache tier, reducing latency and
cost on repeated calls with the same spec / wiki / system prompt.

Strategy
--------
Two cache breakpoints per request (Anthropic supports up to 4):

1. **System prompt** — almost always identical within a task (agent role
   description).  Always marked when ``SWARM_PROMPT_CACHE=1``.
2. **First user message** — contains the spec / wiki / code analysis that
   is re-embedded in both the producer (dev) and reviewer prompts within
   the same pipeline run.  Marked when the message is large enough to
   amortise the cache write cost (≥ ``SWARM_PROMPT_CACHE_MIN_CHARS``,
   default 1024 chars ≈ 256 tokens).

OpenAI / litellm
----------------
OpenAI's prompt caching activates automatically on repeated identical
prefixes — no explicit markup is needed on our side.  We don't send any
extra fields for the OpenAI path.

Env vars
--------
SWARM_PROMPT_CACHE=1              (default ON) — master toggle.
SWARM_PROMPT_CACHE_MIN_CHARS      — min user-message chars to mark (default 1024).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "prompt_caching_enabled",
    "apply_anthropic_cache_control",
]

_CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def prompt_caching_enabled() -> bool:
    """Return True when SWARM_PROMPT_CACHE is not explicitly disabled."""
    return os.getenv("SWARM_PROMPT_CACHE", "1").strip() not in ("0", "false", "no", "off")


def _min_cache_chars() -> int:
    raw = os.getenv("SWARM_PROMPT_CACHE_MIN_CHARS", "1024").strip()
    try:
        return max(256, int(raw))
    except (ValueError, TypeError):
        return 1024


def apply_anthropic_cache_control(
    system_prompt: str,
    chat_messages: list[dict[str, Any]],
) -> tuple[Any, list[dict[str, Any]]]:
    """Return ``(system_param, chat_messages)`` with cache_control added.

    When prompt caching is disabled, returns the system_prompt string and the
    original chat_messages list unchanged.

    When enabled:
    - The system prompt becomes a structured content list with
      ``cache_control: {type: ephemeral}`` so Anthropic caches the entire
      system block.
    - The first user message content block is marked as cacheable when it
      meets the minimum character threshold.

    Args:
        system_prompt:  Plain-text system prompt.
        chat_messages:  Anthropic-formatted message list (each message has
                        ``role`` and ``content`` as a list of typed blocks).

    Returns:
        ``(system_param, mutated_chat_messages)`` where ``system_param`` is
        either a plain string (cache disabled) or a structured list (enabled).
    """
    if not prompt_caching_enabled():
        return system_prompt, chat_messages

    # 1. System prompt cache breakpoint
    system_param: Any = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": _CACHE_CONTROL_EPHEMERAL,
        }
    ]

    # 2. First user message cache breakpoint (when large enough)
    min_chars = _min_cache_chars()
    mutated = [dict(m) for m in chat_messages]  # shallow copy list
    for msg in mutated:
        if msg.get("role") != "user":
            continue
        content_blocks = msg.get("content")
        if not isinstance(content_blocks, list) or not content_blocks:
            break
        first_block = content_blocks[0]
        if not isinstance(first_block, dict):
            break
        text_len = len(str(first_block.get("text") or ""))
        if text_len >= min_chars:
            # Copy the block so we don't mutate the caller's list in place
            new_blocks = [dict(first_block)] + list(content_blocks[1:])
            new_blocks[0]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
            msg["content"] = new_blocks
            logger.debug(
                "prompt_cache: marked first user message block (%d chars) as cacheable",
                text_len,
            )
        break  # only the first user message gets a breakpoint

    return system_param, mutated
