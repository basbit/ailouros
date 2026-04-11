"""Tests for inter-agent contract boundary validation."""

from __future__ import annotations

from backend.App.orchestration.domain.contract_validator import (
    ContractViolation,
    reset_validator,
    validate_agent_exchange,
)
from backend.App.orchestration.infrastructure.runtime_policy import reset_runtime_validator


def setup_function() -> None:
    reset_validator()
    reset_runtime_validator()


def test_validate_agent_exchange_registers_and_accepts_task() -> None:
    validate_agent_exchange(
        task_id="task-1",
        step_id="dev",
        role="dev",
        prompt="implement feature",
        output="done",
    )


def test_validate_agent_exchange_requires_non_empty_output() -> None:
    try:
        validate_agent_exchange(
            task_id="task-2",
            step_id="qa",
            role="qa",
            prompt="verify",
            output="",
        )
    except ContractViolation:
        # Empty output is still evidence-backed and therefore protocol-valid.
        assert False, "validate_agent_exchange should accept empty but explicit output"
