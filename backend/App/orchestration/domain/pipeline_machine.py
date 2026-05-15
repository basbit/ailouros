
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_FIX_CYCLES = 3
DEFAULT_MAX_ATTEMPTS_PER_DEFECT = 3


class PipelinePhase(str, Enum):

    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    QA = "QA"
    FIX = "FIX"
    DONE = "DONE"
    ERROR = "ERROR"


_PHASE_TRANSITIONS: dict[PipelinePhase, frozenset[PipelinePhase]] = {
    PipelinePhase.PLAN: frozenset({PipelinePhase.IMPLEMENT, PipelinePhase.ERROR}),
    PipelinePhase.IMPLEMENT: frozenset({PipelinePhase.VERIFY, PipelinePhase.ERROR}),
    PipelinePhase.VERIFY: frozenset({PipelinePhase.QA, PipelinePhase.FIX, PipelinePhase.DONE, PipelinePhase.ERROR}),
    PipelinePhase.QA: frozenset({PipelinePhase.FIX, PipelinePhase.DONE, PipelinePhase.ERROR}),
    PipelinePhase.FIX: frozenset({PipelinePhase.VERIFY, PipelinePhase.ERROR}),
    PipelinePhase.DONE: frozenset(),
    PipelinePhase.ERROR: frozenset(),
}

_STEP_TO_PHASE: dict[str, PipelinePhase] = {
    "pm": PipelinePhase.PLAN,
    "review_pm": PipelinePhase.PLAN,
    "human_pm": PipelinePhase.PLAN,
    "ba": PipelinePhase.PLAN,
    "review_ba": PipelinePhase.PLAN,
    "human_ba": PipelinePhase.PLAN,
    "architect": PipelinePhase.PLAN,
    "review_stack": PipelinePhase.PLAN,
    "review_arch": PipelinePhase.PLAN,
    "human_arch": PipelinePhase.PLAN,
    "spec_merge": PipelinePhase.PLAN,
    "review_spec": PipelinePhase.PLAN,
    "human_spec": PipelinePhase.PLAN,
    "code_quality_architect": PipelinePhase.PLAN,
    "ux_researcher": PipelinePhase.PLAN,
    "ux_architect": PipelinePhase.PLAN,
    "ui_designer": PipelinePhase.PLAN,
    "image_generator": PipelinePhase.PLAN,
    "audio_generator": PipelinePhase.PLAN,
    "asset_fetcher": PipelinePhase.PLAN,
    "media_generator": PipelinePhase.IMPLEMENT,
    "analyze_code": PipelinePhase.PLAN,
    "generate_documentation": PipelinePhase.PLAN,
    "problem_spotter": PipelinePhase.PLAN,
    "refactor_plan": PipelinePhase.PLAN,
    "human_code_review": PipelinePhase.PLAN,
    "devops": PipelinePhase.PLAN,
    "review_devops": PipelinePhase.PLAN,
    "human_devops": PipelinePhase.PLAN,
    "dev_lead": PipelinePhase.PLAN,
    "review_dev_lead": PipelinePhase.PLAN,
    "human_dev_lead": PipelinePhase.PLAN,
    "dev": PipelinePhase.IMPLEMENT,
    "review_dev": PipelinePhase.VERIFY,
    "visual_probe": PipelinePhase.VERIFY,
    "visual_design_review": PipelinePhase.VERIFY,
    "build_gate": PipelinePhase.VERIFY,
    "spec_gate": PipelinePhase.VERIFY,
    "consistency_gate": PipelinePhase.VERIFY,
    "stub_gate": PipelinePhase.VERIFY,
    "qa": PipelinePhase.QA,
    "review_qa": PipelinePhase.QA,
    "human_qa": PipelinePhase.QA,
    "dev_retry_gate": PipelinePhase.FIX,
    "human_dev": PipelinePhase.FIX,
}


class PipelineMachine:

    def __init__(
        self,
        *,
        max_fix_cycles: int = DEFAULT_MAX_FIX_CYCLES,
        max_attempts_per_defect: int = DEFAULT_MAX_ATTEMPTS_PER_DEFECT,
    ) -> None:
        self._phase: PipelinePhase = PipelinePhase.PLAN
        self._fix_cycles: int = 0
        self._defect_attempts: dict[str, int] = {}
        self._max_fix_cycles = max_fix_cycles
        self._max_attempts_per_defect = max_attempts_per_defect

    @property
    def phase(self) -> PipelinePhase:
        return self._phase

    @property
    def fix_cycles(self) -> int:
        return self._fix_cycles

    @property
    def is_terminal(self) -> bool:
        return self._phase in (PipelinePhase.DONE, PipelinePhase.ERROR)

    def transition(self, target: PipelinePhase, *, source: str = "system") -> None:
        allowed = _PHASE_TRANSITIONS.get(self._phase, frozenset())
        if target not in allowed:
            raise ValueError(
                f"Invalid phase transition: {self._phase.value} -> {target.value} "
                f"(allowed: {[p.value for p in allowed]})"
            )

        if target == PipelinePhase.DONE and source not in ("verification_layer", "system"):
            raise ValueError(
                f"Only verification_layer can transition to DONE, got source={source!r}"
            )

        old = self._phase
        self._phase = target
        if target == PipelinePhase.FIX:
            self._fix_cycles += 1
        logger.info(
            "PipelineMachine: %s -> %s (source=%s, fix_cycles=%d)",
            old.value, target.value, source, self._fix_cycles,
        )

    def step_phase(self, step_id: str) -> PipelinePhase:
        return _STEP_TO_PHASE.get(step_id, PipelinePhase.PLAN)

    def should_stop_fix_cycle(self) -> bool:
        return self._fix_cycles >= self._max_fix_cycles

    def record_defect_attempt(self, category: str) -> bool:
        self._defect_attempts[category] = self._defect_attempts.get(category, 0) + 1
        return self._defect_attempts[category] >= self._max_attempts_per_defect

    def should_change_strategy(self, category: str) -> bool:
        return self._defect_attempts.get(category, 0) >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self._phase.value,
            "fix_cycles": self._fix_cycles,
            "defect_attempts": dict(self._defect_attempts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineMachine:
        machine = cls()
        phase_str = data.get("phase", "PLAN")
        try:
            machine._phase = PipelinePhase(phase_str)
        except ValueError:
            machine._phase = PipelinePhase.PLAN
        machine._fix_cycles = int(data.get("fix_cycles", 0))
        machine._defect_attempts = dict(data.get("defect_attempts") or {})
        return machine


_global_machine: Optional[PipelineMachine] = None


def install_pipeline_machine(machine: PipelineMachine) -> None:
    global _global_machine
    _global_machine = machine


def get_pipeline_machine() -> PipelineMachine:
    global _global_machine
    if _global_machine is None:
        _global_machine = PipelineMachine()
    return _global_machine


def reset_pipeline_machine() -> None:
    global _global_machine
    _global_machine = None
