from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SUMMARIZE_ENABLED_DEFAULT = os.getenv("SWARM_STATE_SUMMARIZE", "0").strip()
_SUMMARIZE_MAX_INPUT = 50_000
_SUMMARIZE_TARGET = 2_000


def state_summarize_enabled() -> bool:
    return _SUMMARIZE_ENABLED_DEFAULT.lower() in ("1", "true", "yes", "on")


def summarize_text(text: str, role_hint: str = "", agent_config: dict[str, Any] | None = None) -> str:
    if len(text) <= _SUMMARIZE_TARGET:
        return text

    truncated_input = text[:_SUMMARIZE_MAX_INPUT]

    try:
        from backend.App.integrations.infrastructure.llm.client import ask_model

        reviewer_cfg: dict[str, Any] = {}
        if isinstance(agent_config, dict):
            reviewer_cfg = agent_config.get("reviewer") or {}

        model = (
            os.getenv("SWARM_STATE_SUMMARIZE_MODEL")
            or reviewer_cfg.get("model")
        )
        if not model:
            raise ValueError("SWARM_STATE_SUMMARIZE_MODEL not set and no reviewer model configured")
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise technical summarizer. "
                    "Summarize the following pipeline output preserving: "
                    "key decisions, requirements, architecture choices, file paths, "
                    "and action items. Drop verbose explanations and formatting. "
                    f"Target: under {_SUMMARIZE_TARGET} characters."
                ),
            },
            {
                "role": "user",
                "content": f"[{role_hint}]\n\n{truncated_input}",
            },
        ]

        result_tuple = ask_model(
            messages=messages,
            model=model,
            temperature=0.1,
        )
        result_text = result_tuple[0] if isinstance(result_tuple, tuple) else str(result_tuple)
        summary = (result_text or "").strip()
        if summary and len(summary) < len(text):
            logger.info(
                "state_summarizer: %s summarized %d → %d chars (model=%s)",
                role_hint, len(text), len(summary), model,
            )
            return summary
    except Exception as exc:
        logger.warning("state_summarizer: LLM call failed for %s: %s", role_hint, exc)

    return text[:_SUMMARIZE_TARGET] + f" … [summarization failed, truncated from {len(text)} chars]"
