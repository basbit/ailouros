from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, field_validator


class ChatMessage(BaseModel):
    role: str
    content: str


class UiRemoteModelsRequest(BaseModel):
    provider: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "swarm-orchestrator"
    messages: list[ChatMessage]
    stream: bool = False
    agent_config: Optional[dict[str, dict[str, Any]]] = None
    pipeline_steps: Optional[list[str]] = None
    pipeline_stages: Optional[list[list[str]]] = None
    pipeline_preset: Optional[str] = None
    scenario_id: Optional[str] = None
    scenario_overrides: Optional[dict[str, dict[str, Any]]] = None
    workspace_root: Optional[str] = None
    workspace_write: bool = False
    project_context_file: Optional[str] = None


class ScenarioPreviewRequest(BaseModel):
    scenario_id: str
    pipeline_steps: Optional[list[str]] = None
    agent_config: Optional[dict[str, dict[str, Any]]] = None
    workspace_write: Optional[bool] = None
    skip_gates: Optional[list[str]] = None
    model_profile: Optional[dict[str, str]] = None


class PipelinePlanRequest(BaseModel):
    goal: str
    constraints: str = ""
    agent_config: Optional[dict[str, dict[str, Any]]] = None


class HumanResumeRequest(BaseModel):
    feedback: str = ""
    stream: bool = True
    agent_config: Optional[dict[str, Any]] = None


class PatternMemoryStoreRequest(BaseModel):
    namespace: str = "default"
    key: str
    value: str
    path: Optional[str] = None
    merge: bool = True


class RetryWith(BaseModel):
    different_model: Optional[str] = None
    tools_off: Optional[bool] = None
    reduced_context: Optional[str] = None


_VALID_REDUCED_CONTEXT_MODES = frozenset({
    "retrieve", "priority_paths", "index_only",
})


class RetryRequest(BaseModel):
    stream: bool = True
    agent_config: Optional[dict[str, Any]] = None
    from_step: Optional[str] = None
    pipeline_steps: Optional[list[str]] = None
    pipeline_stages: Optional[list[list[str]]] = None
    retry_with: Optional[RetryWith] = None

    @field_validator("retry_with", mode="before")
    @classmethod
    def validate_retry_with(cls, v: Optional[RetryWith]) -> Optional[RetryWith]:
        if v is None:
            return v
        set_fields = [
            name for name in ("different_model", "tools_off", "reduced_context")
            if getattr(v, name, None) is not None
        ]
        if len(set_fields) > 1:
            raise ValueError(
                f"retry_with must contain exactly one field; got: {set_fields}"
            )
        if not set_fields:
            return None
        rc = v.reduced_context
        if rc is not None and rc not in _VALID_REDUCED_CONTEXT_MODES:
            raise ValueError(
                f"retry_with.reduced_context must be one of "
                f"{sorted(_VALID_REDUCED_CONTEXT_MODES)}; got: {rc!r}"
            )
        return v


class AgentRoleConfig(BaseModel):
    model_config = {"extra": "allow"}

    environment: Optional[str] = None
    model: Optional[str] = None
    prompt_path: Optional[str] = None
    prompt_text: Optional[str] = None
    auto_approve: Optional[Union[str, bool]] = None
    require_manual: Optional[bool] = None
    remote_profile: Optional[str] = None
    skill_ids: Optional[list[str]] = None


class _ShellConfirmRequest(BaseModel):
    approved: bool


class _ManualShellConfirmRequest(BaseModel):
    done: bool


class _HumanConfirmRequest(BaseModel):
    approved: bool
    user_input: str = ""


def validate_agent_config(agent_config: Optional[dict]) -> None:
    if agent_config is None:
        return
    if not isinstance(agent_config, dict):
        raise ValueError("agent_config must be a JSON object (dict)")
    for role_key, role_val in agent_config.items():
        if not isinstance(role_key, str):
            raise ValueError(f"agent_config key must be a string, got {type(role_key).__name__!r}")
        if role_val is not None and not isinstance(role_val, dict):
            raise ValueError(
                f"agent_config[{role_key!r}] must be a JSON object or null, "
                f"got {type(role_val).__name__!r}"
            )
        if isinstance(role_val, dict):
            try:
                AgentRoleConfig.model_validate(role_val)
            except Exception as exc:
                raise ValueError(f"agent_config[{role_key!r}] is invalid: {exc}") from exc


def validate_pipeline_stages(
    stages: list[list[str]],
    agent_config: Optional[dict] = None,
) -> None:
    validate_agent_config(agent_config)
    from backend.App.orchestration.application.routing.pipeline_graph import (
        validate_pipeline_stages as _validate_stages,
    )
    _validate_stages(stages, agent_config)


class OnboardingApplyRequest(BaseModel):
    workspace_root: str
    content: str


class OnboardingPreconfigureRequest(BaseModel):
    workspace_root: str = ""
    base_model: Optional[str] = None


class MCPServerSpecSchema(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = "npx"
    args: list[str] = []
    enabled: bool = True
    reason: str = ""


class OnboardingMcpApplyRequest(BaseModel):
    workspace_root: str
    servers: Optional[list[MCPServerSpecSchema]] = None
    config: Optional[dict[str, Any]] = None


class OnboardingMcpPreflightRequest(BaseModel):
    workspace_root: str = ""
    tavily_api_key: str = ""
    exa_api_key: str = ""
    scrapingdog_api_key: str = ""


class ProjectSettingsRequest(BaseModel):
    workspace_root: str
    settings: dict[str, Any]


class DesktopProjectInitRequest(BaseModel):
    project_id: str


class BackgroundAgentRequest(BaseModel):
    workspace_root: str = ""
    enabled: bool = False
    watch_paths: str = ""
    environment: str = ""
    model: str = ""
    remote_provider: str = ""
    remote_api_key: str = ""
    remote_base_url: str = ""
