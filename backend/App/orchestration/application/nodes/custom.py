"""Custom role pipeline nodes."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Optional

from backend.App.orchestration.infrastructure.agents.base_agent import load_prompt
from backend.App.orchestration.infrastructure.agents.custom_agent import CustomSwarmRoleAgent
from backend.App.orchestration.application.pipeline_state import PipelineState

from backend.App.orchestration.application.nodes._shared import (
    _documentation_locale_line,
    _remote_api_client_kwargs_for_role,
    _skills_extra_for_role_cfg,
    _swarm_languages_line,
    _swarm_prompt_prefix,
    planning_pipeline_user_context,
)
from backend.App.orchestration.application.nodes._prompt_builders import _run_agent_with_boundary

_CROLE_PREFIX = "crole_"


def custom_role_step_id(role_slug: str) -> str:
    return f"{_CROLE_PREFIX}{role_slug}"


def parse_custom_role_slug(step_id: str) -> Optional[str]:
    if not step_id.startswith(_CROLE_PREFIX):
        return None
    slug = step_id[len(_CROLE_PREFIX):]
    if not slug or not re.match(r"^[a-z0-9_]{1,64}$", slug):
        return None
    return slug


def _custom_role_user_bundle(state: PipelineState) -> str:
    spec = (state.get("spec_output") or "").strip()
    parts = [
        _swarm_prompt_prefix(state),
        _documentation_locale_line(state),
        _swarm_languages_line(state),
        "User task:\n" + planning_pipeline_user_context(state),
    ]
    if spec:
        parts.append(
            "Merged specification (BA + Architect), truncated:\n" + spec[:14000]
        )
    return "\n\n".join(p for p in parts if p)


def _make_custom_role_node(role_slug: str) -> Callable[[PipelineState], dict[str, Any]]:
    def node(state: PipelineState) -> dict[str, Any]:
        step = custom_role_step_id(role_slug)
        agent_config = state.get("agent_config") or {}
        role_config = (agent_config.get("custom_roles") or {}).get(role_slug)
        if not isinstance(role_config, dict):
            return {
                f"{step}_output": (
                    f"[error] Missing agent_config.custom_roles entry for `{role_slug}`."
                ),
                f"{step}_model": "",
                f"{step}_provider": "",
            }
        prompt_text = (role_config.get("prompt_text") or "").strip()
        prompt_path = str(role_config.get("prompt_path") or role_config.get("prompt") or "").strip()
        if prompt_text:
            sys_prompt = prompt_text
        elif prompt_path:
            sys_prompt = load_prompt(prompt_path, "You are a helpful assistant.")
        else:
            sys_prompt = "You are a helpful assistant."
        cfg_model = (role_config.get("model") or "").strip()
        cfg_env = (role_config.get("environment") or "ollama").strip()
        remote_api_kwargs = _remote_api_client_kwargs_for_role(state, role_config)
        agent = CustomSwarmRoleAgent(
            role_id=role_slug,
            system_prompt=sys_prompt,
            model_override=cfg_model or None,
            environment_override=cfg_env or None,
            system_prompt_extra=_skills_extra_for_role_cfg(state, role_config),
            **remote_api_kwargs,
        )
        prompt = _custom_role_user_bundle(state)
        agent_output = _run_agent_with_boundary(state, agent, prompt)
        return {
            f"{step}_output": agent_output,
            f"{step}_model": agent.used_model,
            f"{step}_provider": agent.used_provider,
        }

    return node
