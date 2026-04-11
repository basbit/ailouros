"""Произвольная роль пайплайна из agent_config.custom_roles."""

from __future__ import annotations

import re
from typing import Optional

from backend.App.orchestration.infrastructure.agents.base_agent import BaseAgent, resolve_agent_model, resolve_default_environment


def _safe_custom_role_key(role_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", (role_id or "").strip()) or "CUSTOM"
    return s[:48].upper()


class CustomSwarmRoleAgent(BaseAgent):
    def __init__(
        self,
        *,
        role_id: str,
        system_prompt: str,
        model_override: Optional[str] = None,
        environment_override: Optional[str] = None,
        remote_provider: Optional[str] = None,
        remote_api_key: Optional[str] = None,
        remote_base_url: Optional[str] = None,
        system_prompt_extra: str = "",
    ) -> None:
        rk = _safe_custom_role_key(role_id)
        super().__init__(
            role=f"CUSTOM_{rk}",
            system_prompt=system_prompt,
            model=model_override
            or resolve_agent_model(f"CUSTOM_{rk}"),
            environment=environment_override or resolve_default_environment(),
            remote_provider=remote_provider,
            remote_api_key=remote_api_key,
            remote_base_url=remote_base_url,
            system_prompt_extra=system_prompt_extra,
        )
