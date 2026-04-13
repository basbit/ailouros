"""UX Researcher agent — user behavior analysis, usability testing, research insights."""

from __future__ import annotations

from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


class UXResearcherAgent(BaseAgent):
    def __init__(
        self,
        *,
        system_prompt_path_override: Optional[str] = None,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        system_prompt_extra: str = "",
    ) -> None:
        fallback = (
            "You are a UX Researcher. Conduct user research, validate design decisions, "
            "and provide actionable insights through usability testing and behavioral analysis."
        )
        prompt_path = system_prompt_path_override or "design/design-ux-researcher.md"
        super().__init__(
            role="UX_RESEARCHER",
            system_prompt=load_prompt(prompt_path, fallback),
            model=model_override or resolve_agent_model("UX_RESEARCHER"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )
