"""Pydantic request/response models for the orchestrator API.

Canonical location: backend/UI/REST/schemas.py.
``orchestrator/schemas.py`` is kept as a re-export shim for backward compatibility.
"""

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
    temperature: Optional[float] = None
    agent_config: Optional[dict[str, dict[str, Any]]] = None
    pipeline_steps: Optional[list[str]] = None
    pipeline_stages: Optional[list[list[str]]] = None
    pipeline_preset: Optional[str] = None
    workspace_root: Optional[str] = None
    workspace_write: bool = False
    project_context_file: Optional[str] = None


class PipelinePlanRequest(BaseModel):
    goal: str
    constraints: str = ""
    agent_config: Optional[dict[str, dict[str, Any]]] = None


class HumanResumeRequest(BaseModel):
    """Request body for POST /v1/tasks/{task_id}/human-resume."""

    feedback: str = ""
    stream: bool = True
    agent_config: Optional[dict[str, Any]] = None


class PatternMemoryStoreRequest(BaseModel):
    """Тело POST /v1/pattern-memory/store — запись в JSON-хранилище паттернов."""

    namespace: str = "default"
    key: str
    value: str
    path: Optional[str] = None
    merge: bool = True


class RetryWith(BaseModel):
    """Exactly one field must be set (validated in RetryRequest)."""

    different_model: Optional[str] = None
    tools_off: Optional[bool] = None
    reduced_context: Optional[str] = None  # must be a valid context mode


_VALID_REDUCED_CONTEXT_MODES = frozenset({
    "retrieve", "priority_paths", "index_only",
})


class RetryRequest(BaseModel):
    """Request body for POST /v1/tasks/{task_id}/retry."""

    stream: bool = True
    # Override agent config — use to swap models/providers before retrying
    agent_config: Optional[dict[str, Any]] = None
    # Override the step to start from (defaults to pipeline.json failed_step)
    from_step: Optional[str] = None
    # Override full pipeline steps list (used by "Continue pipeline" to append steps)
    pipeline_steps: Optional[list[str]] = None
    # Staged pipeline: list of stages, each stage is a list of parallel step IDs
    pipeline_stages: Optional[list[list[str]]] = None
    # Managed retry-with policy: exactly ONE field allowed
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
    """Per-role agent configuration. Extra fields are allowed for forward compatibility."""

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
    """Response from the "I can't run this — please run it yourself" dialog.

    ``done=True`` — user clicked Done (executed the command themselves; the
                    side-effects are in place, pipeline continues).
    ``done=False`` — Cancel (pipeline is told the command was not run).
    """
    done: bool


class _HumanConfirmRequest(BaseModel):
    approved: bool
    user_input: str = ""


def validate_agent_config(agent_config: Optional[dict]) -> None:
    """Validate agent_config at API boundary.  Raises ValueError on obviously malformed input.

    Lenient: extra fields are allowed.  Only catches structural problems (wrong types,
    non-dict role values) that would cause opaque KeyError failures deep inside nodes.
    """
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
    """Validate pipeline_stages at API boundary.

    Invariants:
    - stages must be non-empty
    - each stage must be a non-empty list of step IDs
    - no duplicate step IDs across all stages
    - clarify_input must be in the first stage, alone
    - all step IDs must be resolvable

    Raises ValueError with explicit message on any violation.
    """
    if not stages:
        raise ValueError("pipeline_stages must be a non-empty list of stages")

    all_step_ids: list[str] = []
    seen: set[str] = set()

    for stage_idx, stage in enumerate(stages):
        if not isinstance(stage, list) or not stage:
            raise ValueError(
                f"pipeline_stages[{stage_idx}] must be a non-empty list of step IDs"
            )
        for step_id in stage:
            if not isinstance(step_id, str) or not step_id.strip():
                raise ValueError(
                    f"pipeline_stages[{stage_idx}] contains invalid step ID: {step_id!r}"
                )
            if step_id in seen:
                raise ValueError(
                    f"Duplicate step ID {step_id!r} in pipeline_stages"
                )
            seen.add(step_id)
            all_step_ids.append(step_id)

    # Invariant: clarify_input must be first stage, alone
    if "clarify_input" in seen:
        if stages[0] != ["clarify_input"]:
            raise ValueError(
                "clarify_input must be the sole step in the first stage "
                "(cannot be parallelized or moved)"
            )

    # Validate all step IDs are known
    validate_agent_config(agent_config)  # ensure agent_config itself is valid
    from backend.App.orchestration.application.step_registry import (
        validate_pipeline_steps as _validate_steps,
    )
    _validate_steps(all_step_ids, agent_config)


class OnboardingApplyRequest(BaseModel):
    workspace_root: str
    content: str


class OnboardingPreconfigureRequest(BaseModel):
    """Request body for POST /v1/onboarding/preconfigure (G-4)."""

    workspace_root: str = ""
    base_model: Optional[str] = None


class MCPServerSpecSchema(BaseModel):
    """Single MCP server spec as sent by the frontend."""

    name: str
    transport: str = "stdio"
    command: str = "npx"
    args: list[str] = []
    enabled: bool = True
    reason: str = ""


class OnboardingMcpApplyRequest(BaseModel):
    """Request body for POST /v1/onboarding/mcp-config/apply (G-4).

    Frontend sends {workspace_root, servers: [...]}.
    Legacy callers may send {workspace_root, config: {...}}.
    """

    workspace_root: str
    # Frontend format: list of server specs
    servers: Optional[list[MCPServerSpecSchema]] = None
    # Legacy/internal format: raw config dict
    config: Optional[dict[str, Any]] = None


class OnboardingMcpPreflightRequest(BaseModel):
    """Request body for POST /v1/onboarding/mcp-preflight (G-4)."""

    workspace_root: str = ""
    # Web-search API keys entered in the UI settings panel.
    # Passed through to build_preflight_recommendations so the
    # internet_search capability shows green even when the keys
    # are stored in localStorage rather than env vars.
    tavily_api_key: str = ""
    exa_api_key: str = ""
    scrapingdog_api_key: str = ""
