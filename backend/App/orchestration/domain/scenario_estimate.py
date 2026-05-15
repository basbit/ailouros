from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class StepEstimate:
    step_id: str
    estimated_duration_sec: Optional[int]
    essential: bool


@dataclass(frozen=True)
class ScenarioEstimate:
    scenario_id: str
    steps: tuple[StepEstimate, ...]
    total_seconds: Optional[int]
    essential_seconds: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "steps": [
                {
                    "step_id": step.step_id,
                    "estimated_duration_sec": step.estimated_duration_sec,
                    "essential": step.essential,
                }
                for step in self.steps
            ],
            "total_seconds": self.total_seconds,
            "essential_seconds": self.essential_seconds,
        }


def _parse_step_entry(entry: Any) -> tuple[str, Optional[int], bool]:
    if isinstance(entry, str):
        step_id = entry.strip()
        if not step_id:
            raise ValueError("pipeline_steps entry must be a non-empty string")
        return step_id, None, True
    if not isinstance(entry, dict):
        raise ValueError(
            "pipeline_steps entry must be a string or an object with 'id'"
        )
    raw_id = entry.get("id") or entry.get("step_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("pipeline_steps entry object must have non-empty 'id'")
    step_id = raw_id.strip()
    duration_raw = entry.get("estimated_duration_sec")
    if duration_raw is None:
        duration: Optional[int] = None
    else:
        if isinstance(duration_raw, bool) or not isinstance(duration_raw, int):
            raise ValueError(
                f"estimated_duration_sec for {step_id!r} must be an int"
            )
        if duration_raw < 0:
            raise ValueError(
                f"estimated_duration_sec for {step_id!r} must be non-negative"
            )
        duration = duration_raw
    essential_raw = entry.get("essential", True)
    if not isinstance(essential_raw, bool):
        raise ValueError(f"essential for {step_id!r} must be a bool")
    return step_id, duration, essential_raw


def compute_scenario_estimate(scenario_payload: dict[str, Any]) -> ScenarioEstimate:
    if not isinstance(scenario_payload, dict):
        raise ValueError("scenario_payload must be a dict")
    raw_id = scenario_payload.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError("scenario_payload.id must be a non-empty string")
    scenario_id = raw_id.strip()
    raw_steps = scenario_payload.get("pipeline_steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("pipeline_steps must be a non-empty list")
    metadata_lookup: dict[str, dict[str, Any]] = {}
    raw_metadata = scenario_payload.get("step_estimates")
    if raw_metadata is not None:
        if not isinstance(raw_metadata, list):
            raise ValueError("step_estimates must be a list when provided")
        for entry in raw_metadata:
            if not isinstance(entry, dict):
                raise ValueError("step_estimates entry must be an object")
            sid = entry.get("step_id") or entry.get("id")
            if not isinstance(sid, str) or not sid.strip():
                raise ValueError(
                    "step_estimates entry must have non-empty 'step_id'"
                )
            metadata_lookup[sid.strip()] = entry
    steps: list[StepEstimate] = []
    for raw_step in raw_steps:
        step_id, inline_duration, inline_essential = _parse_step_entry(raw_step)
        meta = metadata_lookup.get(step_id)
        if isinstance(raw_step, str) and meta is not None:
            _, duration, essential = _parse_step_entry(
                {"id": step_id, **meta}
            )
        else:
            duration = inline_duration
            essential = inline_essential
        steps.append(
            StepEstimate(
                step_id=step_id,
                estimated_duration_sec=duration,
                essential=essential,
            )
        )
    total_seconds = _aggregate(steps, only_essential=False)
    essential_seconds = _aggregate(steps, only_essential=True)
    return ScenarioEstimate(
        scenario_id=scenario_id,
        steps=tuple(steps),
        total_seconds=total_seconds,
        essential_seconds=essential_seconds,
    )


def _aggregate(steps: list[StepEstimate], only_essential: bool) -> Optional[int]:
    relevant = [s for s in steps if (not only_essential) or s.essential]
    if not relevant:
        return 0
    if any(s.estimated_duration_sec is None for s in relevant):
        return None
    total = 0
    for s in relevant:
        duration = s.estimated_duration_sec
        if duration is not None:
            total += duration
    return total
