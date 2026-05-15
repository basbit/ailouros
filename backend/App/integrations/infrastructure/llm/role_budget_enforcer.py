from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from backend.App.integrations.domain.role_budgets import (
    BudgetExceededError,
    RoleBudget,
)
from backend.App.integrations.infrastructure.llm.prompt_size import (
    estimate_chat_request_size,
)
from backend.App.integrations.infrastructure.llm.token_counter import count_tokens
from backend.App.integrations.infrastructure.role_budgets_loader import (
    get_role_budget,
)

logger = logging.getLogger(__name__)

_OLLAMA_NOTICE_LOCK = threading.Lock()
_OLLAMA_NOTICE_EMITTED: set[str] = set()


def resolve_role_budget(role: Optional[str]) -> Optional[RoleBudget]:
    if not role:
        return None
    budget = get_role_budget(role)
    if budget is None:
        raise BudgetExceededError(
            channel="role",
            used=0,
            cap=0,
            role=role,
        )
    return budget


def enforce_prompt_budget(
    messages: list[dict[str, Any]],
    budget: RoleBudget,
    role: str,
    model: str,
) -> int:
    size = estimate_chat_request_size(messages)
    prompt_tokens = size.approx_tokens_hi
    cap = budget.prompt_tokens_max
    if cap is not None and prompt_tokens > cap:
        raise BudgetExceededError(
            channel="prompt_tokens",
            used=prompt_tokens,
            cap=cap,
            role=role,
        )
    ceiling = budget.total_tokens_ceiling
    if ceiling is not None and prompt_tokens > ceiling:
        raise BudgetExceededError(
            channel="total_tokens_ceiling",
            used=prompt_tokens,
            cap=ceiling,
            role=role,
        )
    logger.info(
        "role_budget: role=%s model=%r prompt_tokens=%d cap=%s ceiling=%s",
        role,
        model,
        prompt_tokens,
        cap,
        ceiling,
    )
    return prompt_tokens


def apply_completion_cap(
    create_kwargs: dict[str, Any],
    budget: RoleBudget,
) -> dict[str, Any]:
    cap = budget.completion_tokens_max
    if cap is None:
        return create_kwargs
    existing = create_kwargs.get("max_tokens")
    if existing is None:
        existing_alt = create_kwargs.get("max_completion_tokens")
        if existing_alt is None or int(existing_alt) > cap:
            create_kwargs["max_tokens"] = cap
    elif int(existing) > cap:
        create_kwargs["max_tokens"] = cap
    return create_kwargs


def verify_total_budget(
    prompt_tokens: int,
    output_text: str,
    budget: RoleBudget,
    role: str,
    model: str,
) -> None:
    output_tokens = count_tokens(output_text, model)
    completion_cap = budget.completion_tokens_max
    if completion_cap is not None and output_tokens > completion_cap:
        raise BudgetExceededError(
            channel="completion_tokens",
            used=output_tokens,
            cap=completion_cap,
            role=role,
        )
    ceiling = budget.total_tokens_ceiling
    if ceiling is None:
        return
    reasoning_estimate = budget.reasoning_tokens_max or 0
    total = prompt_tokens + reasoning_estimate + output_tokens
    if total > ceiling:
        raise BudgetExceededError(
            channel="total_tokens_ceiling",
            used=total,
            cap=ceiling,
            role=role,
        )


def _map_openai_reasoning_effort(reasoning_tokens_max: int) -> str:
    if reasoning_tokens_max <= 1024:
        return "low"
    if reasoning_tokens_max <= 2048:
        return "medium"
    return "high"


def apply_reasoning_channel(
    create_kwargs: dict[str, Any],
    budget: RoleBudget,
    *,
    provider: str,
    model: str,
    role: str,
) -> dict[str, Any]:
    reasoning_cap = budget.reasoning_tokens_max
    if reasoning_cap is None or reasoning_cap <= 0:
        return create_kwargs
    if provider == "anthropic":
        create_kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": reasoning_cap,
        }
        logger.info(
            "role_budget: provider=anthropic role=%s reasoning_budget_tokens=%d",
            role,
            reasoning_cap,
        )
        return create_kwargs
    if provider == "openai":
        create_kwargs["reasoning_effort"] = _map_openai_reasoning_effort(reasoning_cap)
        logger.info(
            "role_budget: provider=openai role=%s reasoning_effort=%s (cap=%d)",
            role,
            create_kwargs["reasoning_effort"],
            reasoning_cap,
        )
        return create_kwargs
    if provider == "ollama":
        key = f"{model}:{role}"
        with _OLLAMA_NOTICE_LOCK:
            already = key in _OLLAMA_NOTICE_EMITTED
            if not already:
                _OLLAMA_NOTICE_EMITTED.add(key)
        if not already:
            logger.info(
                "role_budget: provider=ollama role=%s model=%r — no native reasoning channel; "
                "reasoning_tokens_max=%d requested but not forwarded",
                role,
                model,
                reasoning_cap,
            )
        return create_kwargs
    logger.info(
        "role_budget: provider=%s role=%s — reasoning channel unsupported; "
        "reasoning_tokens_max=%d ignored",
        provider,
        role,
        reasoning_cap,
    )
    return create_kwargs


def reset_ollama_notice_cache() -> None:
    with _OLLAMA_NOTICE_LOCK:
        _OLLAMA_NOTICE_EMITTED.clear()


__all__ = [
    "resolve_role_budget",
    "enforce_prompt_budget",
    "apply_completion_cap",
    "verify_total_budget",
    "apply_reasoning_channel",
    "reset_ollama_notice_cache",
]
