from __future__ import annotations

from typing import Any, cast

from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import DefectReport


def normalize_trusted_verification_commands(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    commands: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        expected = str(item.get("expected") or "").strip()
        if not command or not expected:
            continue
        commands.append({"command": command, "expected": expected})
    return commands


def expected_trusted_verification_commands(state: PipelineState) -> list[dict[str, str]]:
    artifact = state.get("deliverables_artifact")
    if not isinstance(artifact, dict):
        return []
    return normalize_trusted_verification_commands(artifact.get("verification_commands"))


def require_structured_blockers(
    *,
    report: DefectReport,
    verdict: str,
    step_id: str,
) -> None:
    if verdict == "NEEDS_WORK" and not report.has_blockers:
        raise RuntimeError(
            f"{step_id}: reviewer returned NEEDS_WORK without structured P0/P1 defects"
        )


def verification_layer_status_message(
    gate_results: list[dict[str, Any]],
    *,
    context: str | None = None,
) -> str:
    suffix = f" {context}" if context else ""
    gate_names = [
        name for name in (str(r.get("gate_name") or "").strip() for r in gate_results) if name
    ]
    failed_names = [
        name
        for name in (str(r.get("gate_name") or "").strip() for r in gate_results if not bool(r.get("passed", False)))
        if name
    ]
    if failed_names:
        return f"Trusted verification gates found issues{suffix}: " + ", ".join(failed_names)
    if gate_names:
        return f"Trusted verification gates passed{suffix}: " + ", ".join(gate_names)
    return f"Trusted verification gates completed{suffix}"


def is_human_gate_in_pipeline(state: PipelineState, human_step_id: str) -> bool:
    return human_step_id in cast(list[str], state.get("_pipeline_step_ids") or [])
