"""Canonical application-level helpers for machine agent execution."""

from __future__ import annotations

from typing import Any

from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.contract_validator import validate_agent_exchange
from backend.App.orchestration.infrastructure.runtime_policy import get_runtime_validator


def validate_agent_boundary(
    state: PipelineState,
    agent: Any,
    prompt: str,
    output: str,
    *,
    step_id: str | None = None,
) -> None:
    """Validate prompt/output exchange at the current pipeline step boundary."""
    validate_agent_exchange(
        task_id=str(state.get("task_id") or ""),
        step_id=str(step_id or state.get("_current_step_id") or ""),
        role=str(getattr(agent, "role", agent.__class__.__name__)).strip() or agent.__class__.__name__,
        prompt=prompt,
        output=output,
        validator=get_runtime_validator(),
    )


def run_agent_with_boundary(
    state: PipelineState,
    agent: Any,
    prompt: str,
    *,
    step_id: str | None = None,
) -> str:
    """Run a machine agent and validate the boundary in one canonical place."""
    output = agent.run(prompt)
    validate_agent_boundary(state, agent, prompt, output, step_id=step_id)
    return output
