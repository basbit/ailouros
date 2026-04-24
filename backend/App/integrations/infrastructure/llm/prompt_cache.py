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
    if not prompt_caching_enabled():
        return system_prompt, chat_messages

    system_param: Any = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": _CACHE_CONTROL_EPHEMERAL,
        }
    ]

    min_chars = _min_cache_chars()
    mutated = [dict(m) for m in chat_messages]
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
            new_blocks = [dict(first_block)] + list(content_blocks[1:])
            new_blocks[0]["cache_control"] = _CACHE_CONTROL_EPHEMERAL
            msg["content"] = new_blocks
            logger.debug(
                "prompt_cache: marked first user message block (%d chars) as cacheable",
                text_len,
            )
        break

    return system_param, mutated
