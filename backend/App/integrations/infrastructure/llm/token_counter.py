from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SWARM_TOKEN_COUNTER_BACKEND: str = os.getenv("SWARM_TOKEN_COUNTER_BACKEND", "ratio").strip().lower()
_tiktoken_enc: Optional[object] = None


def _get_tiktoken():
    global _tiktoken_enc
    if _tiktoken_enc is None:
        try:
            import tiktoken
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        except (ImportError, Exception):
            _tiktoken_enc = False
    return _tiktoken_enc if _tiktoken_enc is not False else None


def count_tokens(text: str, model: str = "") -> int:
    backend = SWARM_TOKEN_COUNTER_BACKEND

    use_tiktoken = False
    if backend == "tiktoken":
        use_tiktoken = True
    elif backend == "auto":
        lm = model.lower()
        use_tiktoken = any(kw in lm for kw in ("gpt-4", "gpt-3", "text-davinci", "cl100k"))

    if use_tiktoken:
        enc = _get_tiktoken()
        if enc is not None:
            return len(enc.encode(text))
        logger.debug("token_counter: tiktoken requested but not installed, falling back to ratio")

    return estimate_tokens_by_ratio(text, chars_per_token=3)


def estimate_tokens_by_ratio(text: str, *, chars_per_token: int = 3) -> int:
    return max(1, len(text) // max(1, chars_per_token))


def _env_fraction(var: str, default: float) -> float:
    raw = os.getenv(var, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


ROLE_BUDGETS: dict[str, float] = {
    "pm": _env_fraction("SWARM_TOKEN_BUDGET_PM", 0.20),
    "ba": _env_fraction("SWARM_TOKEN_BUDGET_BA", 0.15),
    "arch": _env_fraction("SWARM_TOKEN_BUDGET_ARCH", 0.15),
    "dev": _env_fraction("SWARM_TOKEN_BUDGET_DEV", 0.30),
    "qa": _env_fraction("SWARM_TOKEN_BUDGET_QA", 0.20),
    "reviewer": _env_fraction("SWARM_TOKEN_BUDGET_REVIEWER", 0.10),
    "devops": _env_fraction("SWARM_TOKEN_BUDGET_DEVOPS", 0.10),
}


def get_role_budget(role: str, total_tokens: int) -> int:
    fraction = ROLE_BUDGETS.get(role.lower(), 0.15)
    return int(total_tokens * fraction)


def should_compact_before_step(
    current_tokens: int,
    total_limit: int,
    next_role: str,
) -> bool:
    budget = get_role_budget(next_role, total_limit)
    headroom = total_limit - current_tokens
    threshold = int(budget * 1.5)
    if headroom < threshold:
        logger.info(
            "token_budget: headroom=%d < 1.5×budget=%d for role=%s → compaction recommended "
            "(current=%d limit=%d)",
            headroom,
            threshold,
            next_role,
            current_tokens,
            total_limit,
        )
        return True
    return False
