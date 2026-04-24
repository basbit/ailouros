
from __future__ import annotations

from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, load_prompt, resolve_agent_model, resolve_default_environment


class DevopsAgent(BaseAgent):
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
            "You are a DevOps engineer. From the approved specification and stack (Architect), "
            "describe install/bootstrap commands and, when needed, add files as "
            "<swarm_file path=\"…\">…</swarm_file>. "
            "For auto-executed commands on the host use raw <swarm_shell>…</swarm_shell> or "
            "<swarm-command>…</swarm-command> outside markdown ``` fences."
        )
        prompt_path = system_prompt_path_override or "engineering/devops-setup.md"
        super().__init__(
            role="DEVOPS",
            system_prompt=load_prompt(prompt_path, fallback),
            model=model_override or resolve_agent_model("DEVOPS"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )
