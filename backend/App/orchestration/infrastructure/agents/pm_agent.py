
from __future__ import annotations

import logging

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PMAgent(BaseAgent):

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
            "You are an experienced Project Manager. "
            "Rewrite the user's task as a concrete, prioritized list of development steps "
            "with expected outcomes."
        )
        prompt_path = system_prompt_path_override or "project-management/project-manager-senior.md"
        super().__init__(
            role="PM",
            system_prompt=load_prompt(
                prompt_path,
                fallback,
            ),
            model=model_override or resolve_agent_model("PM"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )

    def run(self, user_input: str, *, _progress_queue: Any = None) -> str:
        return super().run(user_input, _progress_queue=_progress_queue)
