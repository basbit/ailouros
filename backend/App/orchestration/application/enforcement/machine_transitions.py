from __future__ import annotations

import logging
from typing import Any

from backend.App.orchestration.application.pipeline.pipeline_runtime_support import (
    finalize_pipeline_metrics,
)
from backend.App.orchestration.domain.pipeline_machine import PipelineMachine, PipelinePhase

_logger = logging.getLogger(__name__)


def sync_pipeline_machine(state: Any, machine: PipelineMachine) -> None:
    state["pipeline_phase"] = machine.phase.value
    state["pipeline_machine"] = machine.to_dict()


def transition_pipeline_phase(
    state: Any,
    machine: PipelineMachine,
    phase: PipelinePhase,
    *,
    source: str = "system",
) -> None:
    if machine.phase == phase:
        sync_pipeline_machine(state, machine)
        return
    machine.transition(phase, source=source)
    sync_pipeline_machine(state, machine)


def prepare_pipeline_machine_for_step(
    state: Any,
    machine: PipelineMachine,
    step_id: str,
) -> None:
    if step_id == "dev" and machine.phase == PipelinePhase.PLAN:
        transition_pipeline_phase(state, machine, PipelinePhase.IMPLEMENT)
    elif step_id == "qa" and machine.phase in (PipelinePhase.VERIFY, PipelinePhase.IMPLEMENT):
        transition_pipeline_phase(state, machine, PipelinePhase.QA)
    elif step_id == "review_dev" and machine.phase == PipelinePhase.PLAN:
        _logger.info(
            "prepare_pipeline_machine_for_step: review_dev in PLAN phase — advancing PLAN→IMPLEMENT→VERIFY"
        )
        transition_pipeline_phase(state, machine, PipelinePhase.IMPLEMENT)
        transition_pipeline_phase(state, machine, PipelinePhase.VERIFY)


def finalize_pipeline_machine(state: Any, machine: PipelineMachine) -> None:
    if machine.phase in (PipelinePhase.VERIFY, PipelinePhase.QA) and not (state.get("open_defects") or []):
        transition_pipeline_phase(state, machine, PipelinePhase.DONE, source="verification_layer")
    finalize_pipeline_metrics(state)
