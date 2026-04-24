from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.App.orchestration.domain.quality_gate_policy import extract_verdict


@dataclass
class AgentScratchpad:
    notes: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    human_edits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "notes": self.notes,
            "verdicts": self.verdicts,
            "human_edits": self.human_edits,
        }


@dataclass
class AgentProfile:
    step_id: str
    successful_reviews: int = 0
    blocking_reviews: int = 0
    human_edits: int = 0
    scratchpad: AgentScratchpad = field(default_factory=AgentScratchpad)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "successful_reviews": self.successful_reviews,
            "blocking_reviews": self.blocking_reviews,
            "human_edits": self.human_edits,
            "scratchpad": self.scratchpad.to_dict(),
        }


def build_agent_profiles(pipeline_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, AgentProfile] = {}
    for key, value in pipeline_snapshot.items():
        if not key.endswith("_review_output"):
            continue
        output = str(value or "").strip()
        if not output:
            continue
        step_id = key.removesuffix("_review_output")
        profile = profiles.setdefault(step_id, AgentProfile(step_id=step_id))
        verdict = extract_verdict(output)
        profile.scratchpad.verdicts.append(verdict)
        if verdict == "OK":
            profile.successful_reviews += 1
        else:
            profile.blocking_reviews += 1
            profile.scratchpad.notes.append(output[:500])

    for key, value in pipeline_snapshot.items():
        if not key.endswith("_human_output"):
            continue
        output = str(value or "").strip()
        if not output:
            continue
        step_id = key.removesuffix("_human_output")
        profile = profiles.setdefault(step_id, AgentProfile(step_id=step_id))
        profile.human_edits += 1
        profile.scratchpad.human_edits.append(output[:500])

    return {step_id: profile.to_dict() for step_id, profile in sorted(profiles.items())}
