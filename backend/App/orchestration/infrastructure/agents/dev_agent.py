
from __future__ import annotations

import os
from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


def _resolve_dev_max_output_tokens(override: Optional[int]) -> int:
    if override is not None and override > 0:
        return int(override)
    raw = os.getenv("SWARM_DEV_MAX_OUTPUT_TOKENS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 0


class DevAgent(BaseAgent):
    def __init__(
        self,
        *,
        system_prompt_path_override: Optional[str] = None,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        system_prompt_extra: str = "",
    ) -> None:
        fallback = (
            "You are a Software Developer. From the requirements and architecture, "
            "produce COMPLETE, RUNNABLE code for every file in scope. "
            "No placeholder code, no TODO comments without implementation, no stubs."
        )
        prompt_path = system_prompt_path_override or "engineering/engineering-senior-developer.md"
        super().__init__(
            role="DEV",
            system_prompt=load_prompt(
                prompt_path,
                fallback,
            ),
            model=model_override or resolve_agent_model("DEV"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            max_tokens=_resolve_dev_max_output_tokens(max_output_tokens),
            system_prompt_extra=system_prompt_extra,
        )
