from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.App.orchestration.application.enforcement.enforcement_policy import (
    step_dependencies,
)


@dataclass(frozen=True)
class StepOrderViolation:
    step_id: str
    step_index: int
    missing_prerequisite: str
    prerequisite_index: int | None


@dataclass(frozen=True)
class StepOrderReport:
    violations: tuple[StepOrderViolation, ...]

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)

    def format_summary(self) -> str:
        if not self.violations:
            return ""
        lines = [
            f"  - {violation.step_id} (#{violation.step_index + 1}) "
            f"requires {violation.missing_prerequisite!r} before it"
            + (
                f" (currently at #{violation.prerequisite_index + 1})"
                if violation.prerequisite_index is not None
                else " (missing from pipeline)"
            )
            for violation in self.violations
        ]
        return "\n".join(lines)


def analyze_pipeline_step_order(steps: Iterable[str]) -> StepOrderReport:
    step_list = list(steps)
    dependency_rules = step_dependencies()
    violations: list[StepOrderViolation] = []
    for step_index, step_id in enumerate(step_list):
        prerequisites = dependency_rules.get(step_id) or ()
        for prerequisite in prerequisites:
            if prerequisite not in step_list:
                continue
            prerequisite_index = step_list.index(prerequisite)
            if prerequisite_index >= step_index:
                violations.append(
                    StepOrderViolation(
                        step_id=step_id,
                        step_index=step_index,
                        missing_prerequisite=prerequisite,
                        prerequisite_index=prerequisite_index,
                    )
                )
    return StepOrderReport(violations=tuple(violations))
