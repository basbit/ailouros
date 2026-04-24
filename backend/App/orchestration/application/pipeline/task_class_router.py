from __future__ import annotations

import logging
import os
from typing import Any

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    swarm_env_strings_that_mean_enabled,
)
from backend.App.orchestration.application.pipeline.runners_policy import (
    implementation_keywords,
    research_plan_keywords,
    research_plan_step_ids,
)

_logger = logging.getLogger(__name__)


def detect_task_class(user_input: str) -> str:
    lowered = (user_input or "").lower()
    if any(keyword in lowered for keyword in implementation_keywords()):
        return "implementation"
    if any(keyword in lowered for keyword in research_plan_keywords()):
        return "research_plan"
    return "implementation"


def auto_select_pipeline_steps(
    user_input: str,
    agent_config: dict[str, Any],
    default_steps: list[str],
) -> list[str]:
    if os.getenv("SWARM_TASK_CLASS_ROUTER", "1").strip().lower() not in swarm_env_strings_that_mean_enabled():
        return default_steps
    swarm_config = (agent_config or {}).get("swarm") or {}
    explicit_class = str(swarm_config.get("task_class") or "").strip().lower()
    task_class = (
        explicit_class
        if explicit_class in ("research_plan", "implementation")
        else detect_task_class(user_input)
    )
    if task_class == "research_plan":
        research_steps = research_plan_step_ids()
        _logger.info("task_class_router: detected 'research_plan' — using reduced step set %s", research_steps)
        return research_steps
    return default_steps
