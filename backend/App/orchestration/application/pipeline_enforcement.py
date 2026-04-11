"""Shared post-step enforcement services for pipeline runners."""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from backend.App.orchestration.application.pipeline_runtime_support import (
    deliverable_write_mapping as _deliverable_write_mapping,
    finalize_pipeline_metrics as _finalize_pipeline_metrics,
    load_defect_report as _load_defect_report,
    merge_defect_reports as _merge_defect_reports,
    record_open_defects as _record_open_defects,
    record_planning_review_blocker as _record_planning_review_blocker,
)
from backend.App.orchestration.application.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import DefectReport, cluster_defects
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired
from backend.App.orchestration.domain.pipeline_machine import (
    PipelineMachine,
    PipelinePhase,
)

logger = logging.getLogger(__name__)

_CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY: dict[str, str] = {
    "review_pm": "pm_review_output",
    "review_arch": "arch_review_output",
    "review_stack": "stack_review_output",
    "review_spec": "spec_review_output",
}
_PLANNING_REVIEW_RESUME_STEP: dict[str, str] = {
    "review_pm": "human_pm",
    "review_stack": "human_arch",
    "review_arch": "human_arch",
}
_PLANNING_REVIEW_TARGET_STEP: dict[str, str] = {
    "review_pm": "pm",
    "review_stack": "architect",
    "review_arch": "architect",
}


def sync_pipeline_machine(state: PipelineState, machine: PipelineMachine) -> None:
    state["pipeline_phase"] = machine.phase.value
    state["pipeline_machine"] = machine.to_dict()


def transition_pipeline_phase(
    state: PipelineState,
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


def normalize_trusted_verification_commands(raw: Any) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return commands
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


def enforce_planning_review_gate(
    state: PipelineState,
    *,
    step_id: str,
    review_output: str,
) -> None:
    from backend.App.orchestration.domain.quality_gate_policy import extract_verdict

    resume_step = _PLANNING_REVIEW_RESUME_STEP.get(step_id)
    if not resume_step:
        return
    verdict = extract_verdict(review_output or "")
    if verdict != "NEEDS_WORK":
        return
    detail = (
        f"Planning gate: {step_id} returned NEEDS_WORK. "
        "Downstream planning/execution is blocked until explicit human override "
        "or a corrected planning artifact is provided."
    )
    partial_state = {
        _CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY.get(step_id, f"{step_id}_output"): review_output,
    }
    raise HumanApprovalRequired(
        step=step_id,
        detail=detail,
        partial_state=partial_state,
        resume_pipeline_step=resume_step,
    )


def enter_fix_cycle_or_escalate(
    state: PipelineState,
    machine: PipelineMachine,
    report: DefectReport,
    *,
    step_id: str,
) -> None:
    transition_pipeline_phase(state, machine, PipelinePhase.FIX)
    if machine.should_stop_fix_cycle():
        raise HumanApprovalRequired(
            step=step_id,
            detail=(
                f"Fix cycle budget exhausted after {machine.fix_cycles} iterations. "
                "Human intervention is required."
            ),
            partial_state={
                "open_defects": state.get("open_defects") or [],
                "clustered_open_defects": state.get("clustered_open_defects") or [],
            },
            resume_pipeline_step="human_dev",
        )
    defect_clusters = cluster_defects(report.open_p0 + report.open_p1)
    for category in defect_clusters:
        category = category or "uncategorized"
        exceeded = machine.record_defect_attempt(category)
        if exceeded:
            raise HumanApprovalRequired(
                step=step_id,
                detail=(
                    f"Defect category '{category}' exceeded retry budget. "
                    "Human intervention is required."
                ),
                partial_state={
                    "open_defects": state.get("open_defects") or [],
                    "clustered_open_defects": state.get("clustered_open_defects") or [],
                },
                resume_pipeline_step="human_dev",
            )
    sync_pipeline_machine(state, machine)


def run_post_dev_verification_gates(state: PipelineState) -> list[dict[str, Any]]:
    """Apply dev writes and run trusted verification gates before QA."""
    from backend.App.orchestration.domain.gates import (
        DevManifest,
        TRUSTED_VERIFICATION_COMMANDS,
        gates_passed,
        parse_dev_manifest,
        run_all_gates,
    )
    from backend.App.workspace.infrastructure.patch_parser import apply_from_devops_and_dev_outputs

    workspace_root = str(state.get("workspace_root") or "").strip()
    if not workspace_root:
        return []

    workspace_path = Path(workspace_root).resolve()
    workspace_apply_writes = bool(state.get("workspace_apply_writes"))
    workspace_writes: dict[str, Any] = {
        "written": [],
        "patched": [],
        "udiff_applied": [],
        "parsed": 0,
    }
    if workspace_apply_writes:
        workspace_writes = apply_from_devops_and_dev_outputs(
            dict(state),
            workspace_path,
            run_shell=False,
        )
        state["workspace_writes"] = workspace_writes
        if (
            workspace_writes.get("parsed", 0) == 0
            and int(state.get("dev_mcp_write_count") or 0) == 0
        ):
            logger.warning(
                "verification gate: dev step produced no detected workspace writes "
                "(patch-parse=0, mcp_writes=0) — continuing; QA will validate final state"
            )
    mcp_write_actions = state.get("dev_mcp_write_actions")
    if isinstance(mcp_write_actions, list) and mcp_write_actions:
        merged_actions = list(workspace_writes.get("write_actions") or [])
        for action in mcp_write_actions:
            if not isinstance(action, dict):
                continue
            if action not in merged_actions:
                merged_actions.append(action)
        workspace_writes["write_actions"] = merged_actions

    dev_output = str(state.get("dev_output") or "")
    manifest = parse_dev_manifest(dev_output)
    if manifest is None:
        changed_files = []
        for key in ("written", "patched", "udiff_applied"):
            for rel in workspace_writes.get(key, []) or []:
                if rel not in changed_files:
                    changed_files.append(rel)
        manifest = DevManifest(changed_files=changed_files)
    if not manifest.changed_files and workspace_writes.get("written"):
        manifest.changed_files = list(workspace_writes.get("written") or [])

    expected_trusted_commands = expected_trusted_verification_commands(state)
    if expected_trusted_commands:
        unknown = [
            entry["command"]
            for entry in expected_trusted_commands
            if entry["command"] not in TRUSTED_VERIFICATION_COMMANDS
        ]
        if unknown:
            logger.warning(
                "verification contract: unknown trusted verification commands %s "
                "(allowed=%s) — skipping unknown commands",
                unknown, list(TRUSTED_VERIFICATION_COMMANDS),
            )
            expected_trusted_commands = [
                e for e in expected_trusted_commands if e["command"] not in unknown
            ]
        manifest_trusted_commands = normalize_trusted_verification_commands(
            manifest.to_dict().get("trusted_verification_commands")
        )
        if manifest_trusted_commands and manifest_trusted_commands != expected_trusted_commands:
            logger.warning(
                "verification contract: dev manifest trusted_verification_commands "
                "do not match deliverables_artifact.verification_commands — using deliverables version"
            )
        manifest.trusted_verification_commands = list(expected_trusted_commands)

    must_exist_files = state.get("must_exist_files")
    if not isinstance(must_exist_files, list):
        must_exist_files = None
    spec_symbols = state.get("spec_symbols")
    if not isinstance(spec_symbols, list):
        spec_symbols = None
    production_paths = state.get("production_paths")
    if not isinstance(production_paths, list):
        production_paths = None
    placeholder_allow_list = state.get("placeholder_allow_list")
    if not isinstance(placeholder_allow_list, list):
        placeholder_allow_list = None

    results = run_all_gates(
        workspace_root,
        manifest=manifest,
        must_exist_files=must_exist_files,
        spec_symbols=spec_symbols,
        production_paths=production_paths,
        stub_allow_list=placeholder_allow_list,
        workspace_writes=workspace_writes,
    )
    gate_names_run = [result.gate_name for result in results]
    missing_trusted = [
        entry for entry in expected_trusted_commands if entry["command"] not in gate_names_run
    ]
    if missing_trusted:
        logger.warning(
            "verification contract: trusted verification commands declared but not run: %s — continuing",
            [entry["command"] for entry in missing_trusted],
        )
    state["verification_gates"] = [result.to_dict() for result in results]
    state["dev_manifest"] = manifest.to_dict()
    state["verification_contract"] = {
        "expected_trusted_commands": expected_trusted_commands,
        "manifest_trusted_commands": list(manifest.trusted_verification_commands),
        "gates_run": gate_names_run,
    }
    state["deliverable_write_mapping"] = _deliverable_write_mapping(state)
    if not gates_passed(results):
        failed = [result for result in results if not result.passed]
        summary = "; ".join(
            f"{gate.gate_name}: {(gate.errors or [{'error': 'failed'}])[0].get('error', 'failed')}"
            for gate in failed
        )
        logger.warning(
            "verification gates failed before QA: %s — continuing to QA so it can report on failures",
            summary,
        )
        state["verification_gate_warnings"] = summary
    return [result.to_dict() for result in results]


def prepare_pipeline_machine_for_step(
    state: PipelineState,
    machine: PipelineMachine,
    step_id: str,
) -> None:
    if step_id == "dev" and machine.phase == PipelinePhase.PLAN:
        transition_pipeline_phase(state, machine, PipelinePhase.IMPLEMENT)
    elif step_id == "qa" and machine.phase in (PipelinePhase.VERIFY, PipelinePhase.IMPLEMENT):
        transition_pipeline_phase(state, machine, PipelinePhase.QA)


def run_post_step_enforcement(
    state: PipelineState,
    machine: PipelineMachine,
    step_id: str,
    base_agent_config: dict[str, Any],
    resolve_step: Callable[..., Any],
    run_step_with_stream_progress: Callable[..., Any],
    emit_completed: Callable[..., dict[str, Any]],
) -> Generator[dict[str, Any], None, None]:
    if step_id in _PLANNING_REVIEW_TARGET_STEP:
        from backend.App.orchestration.application.graph_builder import _max_step_retries_env
        from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
        from backend.App.orchestration.domain.quality_gate_policy import (
            extract_verdict as _qg_extract_verdict,
            should_retry as _qg_should_retry,
        )
        import os

        review_output_key = _CRITICAL_REVIEW_STEP_TO_OUTPUT_KEY.get(step_id, f"{step_id}_output")
        review_output = str(state.get(review_output_key) or "")
        _record_planning_review_blocker(state, step_id=step_id, review_output=review_output)
        verdict = _qg_extract_verdict(review_output)
        auto_retry_enabled = os.getenv("SWARM_AUTO_RETRY_ON_NEEDS_WORK", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        target_step = _PLANNING_REVIEW_TARGET_STEP[step_id]
        retries = get_step_retries(state, target_step)
        max_retries = _max_step_retries_env()
        decision = _qg_should_retry(verdict, retries, max_retries) if auto_retry_enabled else "escalate"

        while verdict == "NEEDS_WORK" and decision == "retry":
            yield {
                "agent": "orchestrator",
                "status": "progress",
                "message": (
                    f"Planning gate: {step_id} returned NEEDS_WORK "
                    f"(retry {retries + 1}/{max_retries}). Re-running {target_step} with reviewer feedback..."
                ),
            }
            step_retries = dict(state.get("step_retries") or {})
            step_retries[target_step] = retries + 1
            state["step_retries"] = step_retries

            _, target_func = resolve_step(target_step, base_agent_config)
            yield {"agent": target_step, "status": "in_progress", "message": f"{target_step} (planning retry)"}
            yield from run_step_with_stream_progress(target_step, target_func, state)
            yield emit_completed(target_step, state)

            _, review_func = resolve_step(step_id, base_agent_config)
            yield {"agent": step_id, "status": "in_progress", "message": f"{step_id} (planning retry)"}
            yield from run_step_with_stream_progress(step_id, review_func, state)
            yield emit_completed(step_id, state)

            review_output = str(state.get(review_output_key) or "")
            _record_planning_review_blocker(state, step_id=step_id, review_output=review_output)
            verdict = _qg_extract_verdict(review_output)
            retries = get_step_retries(state, target_step)
            decision = _qg_should_retry(verdict, retries, max_retries)

        enforce_planning_review_gate(state, step_id=step_id, review_output=review_output)

    if step_id == "dev":
        gate_results = run_post_dev_verification_gates(state)
        transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
        if gate_results:
            yield {
                "agent": "verification_layer",
                "status": "completed",
                "message": (
                    "Trusted verification gates passed: "
                    + ", ".join(result["gate_name"] for result in gate_results)
                ),
            }

    if step_id == "review_dev":
        from backend.App.orchestration.application.graph_builder import (
            _max_step_retries_env,
            _quality_gate_enabled,
        )
        from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
        from backend.App.orchestration.domain.quality_gate_policy import (
            extract_verdict as _qg_extract_verdict,
            should_retry as _qg_should_retry,
        )

        if _quality_gate_enabled(state):
            verdict = _qg_extract_verdict(state.get("dev_review_output") or "")
            report = _load_defect_report(state, "dev_defect_report")
            _record_open_defects(state, report)
            require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
            dev_retries = get_step_retries(state, "dev")
            max_retries = _max_step_retries_env()
            decision = _qg_should_retry(verdict, dev_retries, max_retries)

            while decision == "retry":
                enter_fix_cycle_or_escalate(state, machine, report, step_id="review_dev")
                yield {
                    "agent": "orchestrator",
                    "status": "progress",
                    "message": (
                        f"Quality gate: review_dev returned NEEDS_WORK "
                        f"(retry {dev_retries + 1}/{max_retries}). Re-running dev..."
                    ),
                }
                step_retries = dict(state.get("step_retries") or {})
                step_retries["dev"] = dev_retries + 1
                state["step_retries"] = step_retries

                _, dev_func = resolve_step("dev", base_agent_config)
                yield {"agent": "dev", "status": "in_progress", "message": "Dev (retry)"}
                yield from run_step_with_stream_progress("dev", dev_func, state)
                yield emit_completed("dev", state)
                run_post_dev_verification_gates(state)
                transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
                yield {
                    "agent": "verification_layer",
                    "status": "completed",
                    "message": "Trusted verification gates passed after dev retry",
                }

                _, review_func = resolve_step("review_dev", base_agent_config)
                yield {"agent": "review_dev", "status": "in_progress", "message": "Review dev (retry)"}
                yield from run_step_with_stream_progress("review_dev", review_func, state)
                yield emit_completed("review_dev", state)

                dev_retries = get_step_retries(state, "dev")
                verdict = _qg_extract_verdict(state.get("dev_review_output") or "")
                report = _load_defect_report(state, "dev_defect_report")
                _record_open_defects(state, report)
                require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
                decision = _qg_should_retry(verdict, dev_retries, max_retries)

            if decision == "escalate":
                raise HumanApprovalRequired(
                    step="review_dev",
                    detail=(
                        f"Quality gate: dev retries exhausted ({dev_retries}/{max_retries}). "
                        "Structured defects require manual intervention."
                    ),
                    partial_state={"open_defects": state.get("open_defects") or []},
                    resume_pipeline_step="human_dev",
                )

    if step_id == "review_qa":
        from backend.App.orchestration.application.graph_builder import (
            _max_step_retries_env,
            _quality_gate_enabled,
        )
        from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
        from backend.App.orchestration.domain.quality_gate_policy import (
            extract_verdict as _qg_extract_verdict,
            should_retry as _qg_should_retry,
        )

        if _quality_gate_enabled(state):
            verdict = _qg_extract_verdict(state.get("qa_review_output") or "")
            qa_report = _load_defect_report(state, "qa_defect_report")
            qa_review_report = _load_defect_report(state, "qa_review_defect_report")
            report = _merge_defect_reports(qa_report, qa_review_report)
            _record_open_defects(state, report)
            require_structured_blockers(report=report, verdict=verdict, step_id="review_qa")
            qa_retries = get_step_retries(state, "qa")
            max_retries = _max_step_retries_env()
            decision = _qg_should_retry(verdict, qa_retries, max_retries)

            while decision == "retry":
                enter_fix_cycle_or_escalate(state, machine, report, step_id="review_qa")
                yield {
                    "agent": "orchestrator",
                    "status": "progress",
                    "message": (
                        f"Quality gate: review_qa returned NEEDS_WORK "
                        f"(retry {qa_retries + 1}/{max_retries}). Re-running dev..."
                    ),
                }
                step_retries = dict(state.get("step_retries") or {})
                step_retries["qa"] = qa_retries + 1
                state["step_retries"] = step_retries

                _, dev_func = resolve_step("dev", base_agent_config)
                yield {"agent": "dev", "status": "in_progress", "message": "Dev (retry from QA)"}
                yield from run_step_with_stream_progress("dev", dev_func, state)
                yield emit_completed("dev", state)
                run_post_dev_verification_gates(state)
                transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
                yield {
                    "agent": "verification_layer",
                    "status": "completed",
                    "message": "Trusted verification gates passed after QA-triggered dev retry",
                }

                transition_pipeline_phase(state, machine, PipelinePhase.QA, source="system")
                _, qa_func = resolve_step("qa", base_agent_config)
                yield {"agent": "qa", "status": "in_progress", "message": "QA (retry)"}
                yield from run_step_with_stream_progress("qa", qa_func, state)
                yield emit_completed("qa", state)

                _, review_func = resolve_step("review_qa", base_agent_config)
                yield {"agent": "review_qa", "status": "in_progress", "message": "Review QA (retry)"}
                yield from run_step_with_stream_progress("review_qa", review_func, state)
                yield emit_completed("review_qa", state)

                qa_retries = get_step_retries(state, "qa")
                verdict = _qg_extract_verdict(state.get("qa_review_output") or "")
                qa_report = _load_defect_report(state, "qa_defect_report")
                qa_review_report = _load_defect_report(state, "qa_review_defect_report")
                report = _merge_defect_reports(qa_report, qa_review_report)
                _record_open_defects(state, report)
                require_structured_blockers(report=report, verdict=verdict, step_id="review_qa")
                decision = _qg_should_retry(verdict, qa_retries, max_retries)

            if decision == "escalate":
                raise HumanApprovalRequired(
                    step="review_qa",
                    detail=(
                        f"Quality gate: QA retries exhausted ({qa_retries}/{max_retries}). "
                        "Structured defects require manual intervention."
                    ),
                    partial_state={"open_defects": state.get("open_defects") or []},
                    resume_pipeline_step="human_qa",
                )


def finalize_pipeline_machine(state: PipelineState, machine: PipelineMachine) -> None:
    if machine.phase in (PipelinePhase.VERIFY, PipelinePhase.QA) and not (state.get("open_defects") or []):
        transition_pipeline_phase(state, machine, PipelinePhase.DONE, source="verification_layer")
    _finalize_pipeline_metrics(state)
