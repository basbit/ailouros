from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _text_from_message(m: Mapping[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        texts = []
        for part in c:
            if isinstance(part, Mapping) and part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
        return "\n".join(texts)
    return ""


@dataclass(frozen=True)
class ChatRequestSize:
    chars_total: int
    chars_system: int
    chars_user: int
    chars_other: int
    bytes_utf8: int
    approx_tokens_lo: int
    approx_tokens_hi: int

    @property
    def approx_tokens_mid(self) -> int:
        return (self.approx_tokens_lo + self.approx_tokens_hi) // 2


def estimate_chat_request_size(messages: list[dict[str, str]]) -> ChatRequestSize:
    chars_system = chars_user = chars_other = 0
    joined: list[str] = []
    for m in messages:
        text = _text_from_message(m)
        joined.append(text)
        role = (m.get("role") or "").lower()
        if role == "system":
            chars_system += len(text)
        elif role == "user":
            chars_user += len(text)
        else:
            chars_other += len(text)
    chars_total = chars_system + chars_user + chars_other
    blob = "\n".join(joined)
    bytes_utf8 = len(blob.encode("utf-8"))
    approx_tokens_lo = max(1, chars_total // 4) if chars_total else 0
    approx_tokens_hi = max(1, (chars_total + 1) // 2) if chars_total else 0
    return ChatRequestSize(
        chars_total=chars_total,
        chars_system=chars_system,
        chars_user=chars_user,
        chars_other=chars_other,
        bytes_utf8=bytes_utf8,
        approx_tokens_lo=approx_tokens_lo,
        approx_tokens_hi=approx_tokens_hi,
    )


def format_size_hint_ru(size: ChatRequestSize, model: str) -> str:
    part_a = (
        f"Request size (estimate): ~{size.chars_total:,} UTF-8 chars, "
        f"~{size.bytes_utf8:,} bytes; tokens approximately "
        f"{size.approx_tokens_lo:,}–{size.approx_tokens_hi:,} "
        f"(midpoint ~{size.approx_tokens_mid:,}). "
        f"By role: system {size.chars_system:,}, user {size.chars_user:,}, "
        f"other {size.chars_other:,}. model={model!r}."
    )
    part_b = (
        "To reduce: shorten system (prompts/), avoid embedding entire documents in user; "
        "move long content to files with a brief reference; use less text or a model with larger context; "
        "after Channel Error in LM Studio — restart/unload the model."
    )
    return f"{part_a} {part_b}"


def log_request_size(model: str, size: ChatRequestSize) -> None:
    logger.info(
        "LLM request size: model=%r chars=%s approx_tokens=%s–%s (system=%s user=%s)",
        model,
        size.chars_total,
        size.approx_tokens_lo,
        size.approx_tokens_hi,
        size.chars_system,
        size.chars_user,
    )


def maybe_warn_context_limit(model: str, size: ChatRequestSize) -> None:
    raw = os.environ.get("SWARM_LLM_CONTEXT_TOKENS", "").strip()
    if not raw.isdigit():
        return
    limit = int(raw)
    if limit <= 0:
        return
    reserve = int(os.environ.get("SWARM_LLM_CONTEXT_RESERVE_TOKENS", "4096") or "4096")
    safe = max(1024, limit - max(0, reserve))
    if size.approx_tokens_hi > safe:
        logger.warning(
            "Prompt may not fit in context: upper token estimate %s > limit-reserve %s "
            "(SWARM_LLM_CONTEXT_TOKENS=%s). model=%r",
            size.approx_tokens_hi,
            safe,
            limit,
            model,
        )
