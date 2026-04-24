
from __future__ import annotations

from typing import Any, Optional

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState


def _workspace_root_str(state: PipelineState) -> str:
    return str(state.get("workspace_root") or "").strip()


def reviewer_cfg(state: PipelineState) -> dict[str, Any]:
    return (state.get("agent_config") or {}).get("reviewer") or {}


def _remote_api_client_kwargs(state: PipelineState) -> dict[str, Any]:
    agent_config = state.get("agent_config") or {}
    remote_api = agent_config.get("remote_api")
    legacy_cloud = agent_config.get("cloud")
    provider = ""
    api_key = ""
    base_url = ""
    if isinstance(remote_api, dict):
        provider = str(remote_api.get("provider") or "").strip().lower()
        api_key = str(remote_api.get("api_key") or "").strip()
        base_url = str(remote_api.get("base_url") or "").strip()
    if isinstance(legacy_cloud, dict):
        if not api_key:
            api_key = str(legacy_cloud.get("api_key") or "").strip()
        if not base_url:
            base_url = str(legacy_cloud.get("base_url") or "").strip()
        if not provider and (api_key or base_url):
            provider = "anthropic"
    if not provider and (api_key or base_url):
        provider = "anthropic"
    client_kwargs: dict[str, Any] = {}
    if provider:
        client_kwargs["remote_provider"] = provider
    if api_key:
        client_kwargs["remote_api_key"] = api_key
    if base_url:
        client_kwargs["remote_base_url"] = base_url
    return client_kwargs


def remote_api_kwargs_for_role(
    state: PipelineState,
    role_cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    base_kwargs = _remote_api_client_kwargs(state)
    if not isinstance(role_cfg, dict):
        return base_kwargs
    profile_name = str(
        role_cfg.get("remote_profile") or role_cfg.get("remote_api_profile") or ""
    ).strip()
    if not profile_name:
        return base_kwargs
    agent_config = state.get("agent_config") or {}
    profiles = agent_config.get("remote_api_profiles")
    if not isinstance(profiles, dict):
        return base_kwargs
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        return base_kwargs
    provider = str(profile.get("provider") or "").strip().lower()
    api_key = str(profile.get("api_key") or "").strip()
    base_url = str(profile.get("base_url") or "").strip()
    client_kwargs = dict(base_kwargs)
    if provider:
        client_kwargs["remote_provider"] = provider
    if api_key:
        client_kwargs["remote_api_key"] = api_key
    if base_url:
        client_kwargs["remote_base_url"] = base_url
    return client_kwargs


def skills_extra(
    state: PipelineState,
    role_cfg: Optional[dict[str, Any]] = None,
) -> str:
    from backend.App.integrations.infrastructure.skill_repository import format_role_skills_extra

    agent_config = state.get("agent_config") or {}
    if not isinstance(agent_config, dict):
        return ""
    cfg = role_cfg if isinstance(role_cfg, dict) else None
    return format_role_skills_extra(
        agent_config, cfg, workspace_root=_workspace_root_str(state)
    )
