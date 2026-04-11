"""P0.5: Deterministic state machine for pipeline execution.

Phases: PLAN -> IMPLEMENT -> VERIFY -> QA -> FIX -> DONE
Only the verification layer can transition to DONE.

The machine tracks the current phase, enforces valid transitions,
and provides anti-loop protection (P0 risk mitigation).
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PipelinePhase(str, Enum):
    """Pipeline execution phases per improve-plan P0.5."""

    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    QA = "QA"
    FIX = "FIX"
    DONE = "DONE"
    ERROR = "ERROR"


# Valid phase transitions (source -> allowed targets)
_PHASE_TRANSITIONS: dict[PipelinePhase, frozenset[PipelinePhase]] = {
    PipelinePhase.PLAN: frozenset({PipelinePhase.IMPLEMENT, PipelinePhase.ERROR}),
    PipelinePhase.IMPLEMENT: frozenset({PipelinePhase.VERIFY, PipelinePhase.ERROR}),
    PipelinePhase.VERIFY: frozenset({PipelinePhase.QA, PipelinePhase.FIX, PipelinePhase.DONE, PipelinePhase.ERROR}),
    PipelinePhase.QA: frozenset({PipelinePhase.FIX, PipelinePhase.DONE, PipelinePhase.ERROR}),
    PipelinePhase.FIX: frozenset({PipelinePhase.VERIFY, PipelinePhase.ERROR}),
    PipelinePhase.DONE: frozenset(),   # terminal
    PipelinePhase.ERROR: frozenset(),  # terminal
}

# Map pipeline step IDs to their phase
_STEP_TO_PHASE: dict[str, PipelinePhase] = {
    # Planning steps
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
    # Implementation
    "dev": PipelinePhase.IMPLEMENT,
    # Verification
    "review_dev": PipelinePhase.VERIFY,
    "build_gate": PipelinePhase.VERIFY,
    "spec_gate": PipelinePhase.VERIFY,
    "consistency_gate": PipelinePhase.VERIFY,
    "stub_gate": PipelinePhase.VERIFY,
    # QA
    "qa": PipelinePhase.QA,
    "review_qa": PipelinePhase.QA,
    "human_qa": PipelinePhase.QA,
    # Fix cycle
    "dev_retry_gate": PipelinePhase.FIX,
    "human_dev": PipelinePhase.FIX,
}


def _max_fix_cycles() -> int:
    """Maximum number of FIX->VERIFY->QA cycles before forced stop."""
    try:
        return int(os.getenv("SWARM_MAX_FIX_CYCLES", "3"))
    except ValueError:
        return 3


def _max_attempts_per_defect() -> int:
    """Max retry attempts for a single defect class before strategy change."""
    try:
        return int(os.getenv("SWARM_MAX_ATTEMPTS_PER_DEFECT", "3"))
    except ValueError:
        return 3


class PipelineMachine:
    """Deterministic state machine for pipeline execution.

    Tracks current phase and enforces valid transitions.
    Only VERIFY phase can transition to DONE.
    """

    def __init__(self) -> None:
        self._phase: PipelinePhase = PipelinePhase.PLAN
        self._fix_cycles: int = 0
        self._defect_attempts: dict[str, int] = {}  # defect_category -> attempts

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
        """Transition to a new phase.

        Args:
            target: The target phase.
            source: Who initiated the transition (for logging).

        Raises:
            ValueError: If the transition is not allowed.
        """
        allowed = _PHASE_TRANSITIONS.get(self._phase, frozenset())
        if target not in allowed:
            raise ValueError(
                f"Invalid phase transition: {self._phase.value} -> {target.value} "
                f"(allowed: {[p.value for p in allowed]})"
            )

        # Only verification layer can transition to DONE
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
        """Return the expected phase for a given pipeline step."""
        return _STEP_TO_PHASE.get(step_id, PipelinePhase.PLAN)

    def should_stop_fix_cycle(self) -> bool:
        """Check if we've exceeded the maximum fix cycles (anti-loop)."""
        return self._fix_cycles >= _max_fix_cycles()

    def record_defect_attempt(self, category: str) -> bool:
        """Record a fix attempt for a defect category.

        Returns True if the category has exceeded max attempts
        (should change strategy per plan risk mitigation).
        """
        self._defect_attempts[category] = self._defect_attempts.get(category, 0) + 1
        return self._defect_attempts[category] >= _max_attempts_per_defect()

    def should_change_strategy(self, category: str) -> bool:
        """Check if strategy change is needed for this defect category."""
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


# Module-level singleton
_global_machine: Optional[PipelineMachine] = None


def get_pipeline_machine() -> PipelineMachine:
    """Return the global PipelineMachine singleton."""
    global _global_machine
    if _global_machine is None:
        _global_machine = PipelineMachine()
    return _global_machine


def reset_pipeline_machine() -> None:
    """Reset the global machine (for testing / new pipeline runs)."""
    global _global_machine
    _global_machine = None
