"""Shared post-step enforcement services for pipeline runners."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, cast

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
    "review_ba": "ba_review_output",
    "review_arch": "arch_review_output",
    "review_stack": "stack_review_output",
    "review_spec": "spec_review_output",
    "review_dev_lead": "dev_lead_review_output",
    "review_pm_tasks": "dev_lead_review_output",
    "review_devops": "devops_review_output",
}
_PLANNING_REVIEW_RESUME_STEP: dict[str, str] = {
    "review_pm": "human_pm",
    "review_ba": "human_ba",
    "review_stack": "human_arch",
    "review_arch": "human_arch",
    "review_dev_lead": "human_dev_lead",
    "review_pm_tasks": "human_dev_lead",
    "review_devops": "human_devops",
}
# review_* → the agent step whose output is being reviewed. On NEEDS_WORK this
# is the step the enforcement loop re-runs with the reviewer's feedback.
# Missing entries here used to make the reviewer's NEEDS_WORK verdict
# effectively a no-op (fixed 2026-04-16: review_ba, review_devops).
_PLANNING_REVIEW_TARGET_STEP: dict[str, str] = {
    "review_pm": "pm",
    "review_ba": "ba",
    "review_stack": "architect",
    "review_arch": "architect",
    "review_dev_lead": "dev_lead",
    "review_pm_tasks": "dev_lead",
    "review_devops": "devops",
}

# Minimum characters in a review output to be considered "real" content
# (review-rules §2: fail fast by default). A local 9B model that replies
# just "VERDICT: NEEDS_WORK" with no analysis does not warrant another
# round of retries — we escalate instead. Configurable for tests.
_MIN_REVIEW_CONTENT_CHARS: int = int(os.getenv("SWARM_MIN_REVIEW_CONTENT_CHARS", "120"))


def _max_planning_review_retries() -> int:
    """Planning-review-specific retry cap, falling back to step-wide default.

    Resolution order:
      1. ``SWARM_MAX_PLANNING_RETRIES`` (non-negative int) — specific.
      2. ``SWARM_MAX_STEP_RETRIES`` (non-negative int) — project-wide.
      3. ``2`` — safe default for reasoning-heavy local models.
    """
    for var in ("SWARM_MAX_PLANNING_RETRIES", "SWARM_MAX_STEP_RETRIES"):
        raw = os.getenv(var, "").strip()
        if not raw:
            continue
        try:
            val = int(raw)
        except ValueError:
            logger.warning("%s=%r is not an int, ignoring", var, raw)
            continue
        return max(0, val)
    return 2


def _is_empty_review(review_output: str | None) -> bool:
    """Return True if reviewer output is too short to carry meaningful feedback.

    Threshold is ``_MIN_REVIEW_CONTENT_CHARS`` — anything shorter is
    treated as a non-review (e.g. bare "VERDICT: NEEDS_WORK" with no
    analysis). Used to short-circuit the retry loop.
    """
    if not review_output:
        return True
    return len(review_output.strip()) < _MIN_REVIEW_CONTENT_CHARS


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


def verification_layer_status_message(
    gate_results: list[dict[str, Any]],
    *,
    context: str | None = None,
) -> str:
    gate_names = [str(result.get("gate_name") or "").strip() for result in gate_results]
    gate_names = [name for name in gate_names if name]
    suffix = f" {context}" if context else ""
    failed = [
        str(result.get("gate_name") or "").strip()
        for result in gate_results
        if not bool(result.get("passed", False))
    ]
    failed = [name for name in failed if name]
    if failed:
        return f"Trusted verification gates found issues{suffix}: " + ", ".join(failed)
    if gate_names:
        return f"Trusted verification gates passed{suffix}: " + ", ".join(gate_names)
    return f"Trusted verification gates completed{suffix}"


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

    # Only block for human approval when the human gate step is actually in
    # the user's pipeline.  If they chose not to include human_dev_lead (etc.),
    # forcing a human pause is unexpected — log a warning and continue.
    pipeline_step_ids = cast(list[str], state.get("_pipeline_step_ids") or [])
    if resume_step not in pipeline_step_ids:
        logger.warning(
            "Planning gate: %s returned NEEDS_WORK but human gate %r is NOT in "
            "the pipeline — continuing without blocking. "
            "Add %r to the pipeline if you want manual review.",
            step_id, resume_step, resume_step,
        )
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


def _should_block_for_human(state: PipelineState, human_step: str) -> bool:
    """Return True only when the human gate is actually in the user's pipeline."""
    pipeline_step_ids = cast(list[str], state.get("_pipeline_step_ids") or [])
    return human_step in pipeline_step_ids


def enter_fix_cycle_or_escalate(
    state: PipelineState,
    machine: PipelineMachine,
    report: DefectReport,
    *,
    step_id: str,
) -> None:
    transition_pipeline_phase(state, machine, PipelinePhase.FIX)
    if machine.should_stop_fix_cycle():
        if _should_block_for_human(state, "human_dev"):
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
        logger.warning(
            "Fix cycle budget exhausted after %d iterations but human_dev not in pipeline — continuing",
            machine.fix_cycles,
        )
        return
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
        parse_dev_manifest,
    )
    from backend.App.orchestration.application.gate_runner import (
        gates_passed,
        run_all_gates,
    )
    from backend.App.workspace.infrastructure.patch_parser import apply_from_devops_and_dev_outputs

    # §ADR Phase 2 — cast once for ephemeral-key writes (workspace_writes,
    # dev_workspace_diff, _post_write_issues, verification_gate_warnings).
    state_d = cast(dict[str, Any], state)
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
        state_d["workspace_writes"] = workspace_writes
        # Capture diff for human review gate display
        from backend.App.workspace.infrastructure.workspace_diff import capture_workspace_diff
        written = list(workspace_writes.get("written") or [])
        patched = list(workspace_writes.get("patched") or [])
        udiff_applied = list(workspace_writes.get("udiff_applied") or [])
        all_changed = sorted(set(written + patched + udiff_applied))
        state_d["dev_workspace_diff"] = capture_workspace_diff(workspace_path, all_changed)

        # ── Post-write integrity checks ──────────────────────────────
        write_errors = list(workspace_writes.get("errors") or [])
        if write_errors:
            logger.warning(
                "Post-dev verification: %d write error(s): %s",
                len(write_errors), write_errors,
            )
            state_d.setdefault("_post_write_issues", []).append(
                f"file_write_integrity: {write_errors}"
            )

        # Healed swarm_patch → swarm_file promotions are informational only
        # (the file was still created). Surface them so the dev retry prompt
        # can teach the model the correct tag on the next round.
        healed_patches = list(workspace_writes.get("healed_patches") or [])
        if healed_patches:
            logger.info(
                "Post-dev verification: %d swarm_patch block(s) auto-healed to "
                "swarm_file creates: %s", len(healed_patches), healed_patches,
            )
            state_d["_swarm_patch_healed_files"] = healed_patches

        # Binary assets the dev tried to author as text cannot be fixed by
        # re-prompting — they need the asset pipeline (see future-plan §23).
        # Emit a distinct signal so the retry loop does not flood the model
        # with "no ======= separator" errors for a .png file.
        binary_assets = list(workspace_writes.get("binary_assets_requested") or [])
        if binary_assets:
            logger.warning(
                "Post-dev verification: %d binary asset(s) requested via text tag — "
                "asset pipeline not yet implemented, see future-plan §23: %s",
                len(binary_assets), binary_assets,
            )
            state_d.setdefault("_post_write_issues", []).append(
                f"binary_asset_requested: {binary_assets}"
            )
            state_d["_binary_assets_needed"] = binary_assets

        # Verify written files actually exist on disk
        missing_after_write = []
        for rel_path in written:
            full_path = workspace_path / rel_path
            if not full_path.is_file():
                missing_after_write.append(rel_path)
        if missing_after_write:
            logger.error(
                "Post-dev verification: %d file(s) missing after write: %s",
                len(missing_after_write), missing_after_write,
            )
            state_d.setdefault("_post_write_issues", []).append(
                f"file_existence_check: {missing_after_write}"
            )
        # ── End post-write integrity checks ──────────────────────────

        if (
            workspace_writes.get("parsed", 0) == 0
            and int(cast(Any, state.get("dev_mcp_write_count")) or 0) == 0
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
        stub_allow_list=cast(Any, placeholder_allow_list),
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
    state_d["verification_gates"] = [result.to_dict() for result in results]
    state_d["dev_manifest"] = manifest.to_dict()
    state_d["verification_contract"] = {
        "expected_trusted_commands": expected_trusted_commands,
        "manifest_trusted_commands": list(manifest.trusted_verification_commands),
        "gates_run": gate_names_run,
    }
    state_d["deliverable_write_mapping"] = _deliverable_write_mapping(state)

    # Append post-write integrity issues to gate warnings
    post_write_issues = state_d.pop("_post_write_issues", None)
    if post_write_issues:
        existing_warnings = state_d.get("verification_gate_warnings", "")
        integrity_summary = "; ".join(post_write_issues)
        state_d["verification_gate_warnings"] = (
            f"{existing_warnings}; {integrity_summary}" if existing_warnings
            else integrity_summary
        )
        logger.warning("Post-write integrity issues added to gate warnings: %s", integrity_summary)

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
        existing = state_d.get("verification_gate_warnings", "")
        state_d["verification_gate_warnings"] = (
            f"{existing}; {summary}" if existing else summary
        )
    return [result.to_dict() for result in results]


_SWARM_FILE_MIN_LINES = int(os.getenv("SWARM_FILE_TAG_MIN_LINES", "20"))
_CODE_FENCE_RE = re.compile(
    r"```(?P<lang>[a-zA-Z0-9_.+-]*)\n(?P<body>.*?)```",
    re.DOTALL,
)
_SWARM_FILE_TAG_RE = re.compile(r"<swarm_file\s+path=", re.IGNORECASE)


def _output_has_unwrapped_code(dev_output: str) -> bool:
    """Return True when dev_output has code fences > _SWARM_FILE_MIN_LINES lines
    that are NOT preceded by a ``<swarm_file path=…>`` tag on a nearby line.

    Enabled only when SWARM_ENFORCE_SWARM_FILE_TAGS=1 (env, default: 0).
    """
    if os.getenv("SWARM_ENFORCE_SWARM_FILE_TAGS", "0").strip() not in ("1", "true", "yes"):
        return False
    # Quick bypass: if swarm_file tags cover all large code blocks, trust the agent
    swarm_tag_count = len(_SWARM_FILE_TAG_RE.findall(dev_output))
    if swarm_tag_count > 0:
        # Count large code blocks; if all are wrapped, skip detailed check
        large_blocks = sum(
            1 for m in _CODE_FENCE_RE.finditer(dev_output)
            if (m.group("body") or "").count("\n") >= _SWARM_FILE_MIN_LINES
        )
        if swarm_tag_count >= large_blocks:
            return False
    for m in _CODE_FENCE_RE.finditer(dev_output):
        body = m.group("body") or ""
        if body.count("\n") >= _SWARM_FILE_MIN_LINES:
            return True
    return False


def _enforce_swarm_file_tags(
    state: "PipelineState",
    *,
    resolve_step: Callable,
    base_agent_config: dict,
    run_step_with_stream_progress: Callable,
    emit_completed: Callable,
) -> "Generator[dict, None, None]":
    """§10.4 — Yield enforcement events; re-prompt Dev once if code fences lack <swarm_file> wrappers."""
    dev_output = str(state.get("dev_output") or "")
    if not _output_has_unwrapped_code(dev_output):
        return

    logger.warning(
        "§10.4 swarm_file enforcement: dev_output contains code fences > %d lines "
        "without <swarm_file> wrappers — re-prompting Dev once.",
        _SWARM_FILE_MIN_LINES,
    )
    yield {
        "agent": "orchestrator",
        "status": "progress",
        "message": (
            "⚠ Dev output contains code blocks without <swarm_file path='...'> wrappers. "
            "Re-prompting Dev to wrap all file content correctly."
        ),
    }
    # Inject re-prompt instruction into state so the dev node picks it up
    cast(dict[str, Any], state)["_swarm_file_reprompt"] = (
        "Your previous output contained code blocks that were NOT wrapped in "
        "<swarm_file path='relative/path'> tags. "
        "This is required for the workspace artifact tracker to record which files you changed. "
        "Please re-output EVERY file you intend to write, each wrapped in "
        "<swarm_file path='path/relative/to/workspace'>…content…</swarm_file>. "
        "Do not repeat unchanged files. Only include files that require changes."
    )
    try:
        _, dev_func = resolve_step("dev", base_agent_config)
    except Exception as exc:
        logger.warning("swarm_file enforcement: could not resolve dev step: %s", exc)
        return
    yield {"agent": "dev", "status": "in_progress", "message": "dev (swarm_file re-wrap)"}
    yield from run_step_with_stream_progress("dev", dev_func, state)
    yield emit_completed("dev", state)
    cast(dict[str, Any], state).pop("_swarm_file_reprompt", None)


def prepare_pipeline_machine_for_step(
    state: Any,
    machine: PipelineMachine,
    step_id: str,
) -> None:
    if step_id == "dev" and machine.phase == PipelinePhase.PLAN:
        transition_pipeline_phase(state, machine, PipelinePhase.IMPLEMENT)
    elif step_id == "qa" and machine.phase in (PipelinePhase.VERIFY, PipelinePhase.IMPLEMENT):
        transition_pipeline_phase(state, machine, PipelinePhase.QA)


def run_post_step_enforcement(
    state: Any,
    machine: PipelineMachine,
    step_id: str,
    base_agent_config: dict[str, Any],
    resolve_step: Callable[..., Any],
    run_step_with_stream_progress: Callable[..., Any],
    emit_completed: Callable[..., dict[str, Any]],
) -> Generator[dict[str, Any], None, None]:
    if step_id in _PLANNING_REVIEW_TARGET_STEP:
        from backend.App.orchestration.application.pipeline_state_helpers import get_step_retries
        from backend.App.orchestration.domain.quality_gate_policy import (
            extract_verdict as _qg_extract_verdict,
            should_retry as _qg_should_retry,
        )
        # NOTE: ``os`` is already imported at module level. Do NOT re-import
        # inside the function — Python would then treat ``os`` as a local in
        # the entire function body, and every branch that uses ``os.getenv``
        # but does not hit this ``if step_id in _PLANNING_REVIEW_TARGET_STEP``
        # path (e.g. the ``review_dev`` branch at the stale-review similarity
        # check) would raise ``UnboundLocalError: local variable 'os'
        # referenced before assignment``.

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
        max_retries = _max_planning_review_retries()
        decision = _qg_should_retry(verdict, retries, max_retries) if auto_retry_enabled else "escalate"

        # §3 Empty-review short-circuit: a bare "VERDICT: NEEDS_WORK" with
        # no analysis is not actionable — escalate without spending another
        # round of LLM budget on a retry. See bug aec02899 (108k tokens burned
        # because local 9B kept emitting canned near-empty reviews).
        if verdict == "NEEDS_WORK" and _is_empty_review(review_output):
            logger.warning(
                "Planning gate: %s returned NEEDS_WORK with empty/short review "
                "(%d chars < %d threshold) — escalating without retry (task_id=%s)",
                step_id, len(review_output.strip()), _MIN_REVIEW_CONTENT_CHARS,
                (state.get("task_id") or "")[:36],
            )
            yield {
                "agent": "orchestrator",
                "status": "progress",
                "message": (
                    f"Planning gate: {step_id} returned NEEDS_WORK but review is "
                    f"empty/too short ({len(review_output.strip())} chars). "
                    "Escalating without retry — check reviewer prompt/model."
                ),
            }
            decision = "escalate"

        prev_review_text = review_output

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

            # Ensure reviewer feedback is stored in state BEFORE re-running the target step.
            # This allows pm_node/arch_node to pick it up via state["planning_review_feedback"].
            feedback = dict(state.get("planning_review_feedback") or {})
            if target_step and review_output:
                feedback[target_step] = review_output
                state["planning_review_feedback"] = feedback
                logger.info(
                    "Planning gate: injected %d chars of reviewer feedback for %s retry",
                    len(review_output), target_step,
                )

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

            # Stale-review detection: if the reviewer produced near-identical output
            # to the previous iteration, the model is hallucinating stale feedback
            # instead of evaluating the updated artifact. Auto-approve to break the loop.
            if verdict == "NEEDS_WORK" and prev_review_text:
                _stale_threshold = float(
                    os.getenv("SWARM_STALE_REVIEW_SIMILARITY_THRESHOLD", "0.85").strip()
                )
                from difflib import SequenceMatcher
                similarity = SequenceMatcher(None, prev_review_text, review_output).ratio()
                if similarity > _stale_threshold:
                    logger.warning(
                        "Planning gate: %s reviewer produced near-identical review "
                        "(%.0f%% similar, threshold=%.0f%%) — auto-approving to "
                        "break hallucination loop. task_id=%s",
                        step_id, similarity * 100, _stale_threshold * 100,
                        (state.get("task_id") or "")[:36],
                    )
                    verdict = "OK"
            # Empty-review short-circuit inside the loop too: if a retry
            # produced a one-liner verdict we treat it like the initial
            # empty case and escalate instead of continuing to burn tokens.
            if verdict == "NEEDS_WORK" and _is_empty_review(review_output):
                logger.warning(
                    "Planning gate: %s retry produced empty/short review "
                    "(%d chars) — escalating task_id=%s",
                    step_id, len(review_output.strip()),
                    (state.get("task_id") or "")[:36],
                )
                yield {
                    "agent": "orchestrator",
                    "status": "progress",
                    "message": (
                        f"Planning gate: {step_id} retry returned empty/short review "
                        f"({len(review_output.strip())} chars). Escalating."
                    ),
                }
                break

            prev_review_text = review_output

            retries = get_step_retries(state, target_step)
            decision = _qg_should_retry(verdict, retries, max_retries)

        if verdict == "NEEDS_WORK":
            yield {
                "agent": "orchestrator",
                "status": "progress",
                "message": (
                    f"Planning gate: {step_id} still NEEDS_WORK after {max_retries} retries. "
                    f"Proceeding with current output. Consider human review."
                ),
            }

        enforce_planning_review_gate(state, step_id=step_id, review_output=review_output)

    if step_id == "dev":
        # §10.4 — Enforce <swarm_file> tagging for all file writes.
        # If the Dev output contains large code fences that lack <swarm_file> wrappers,
        # re-prompt once with an explicit instruction to add them.
        _yield_from_swarm_file_enforcement = _enforce_swarm_file_tags(
            state,
            resolve_step=resolve_step,
            base_agent_config=base_agent_config,
            run_step_with_stream_progress=run_step_with_stream_progress,
            emit_completed=emit_completed,
        )
        yield from _yield_from_swarm_file_enforcement

        gate_results = run_post_dev_verification_gates(state)
        transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
        if gate_results:
            yield {
                "agent": "verification_layer",
                "status": "completed",
                "message": verification_layer_status_message(gate_results),
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

            prev_dev_review_text = str(state.get("dev_review_output") or "")

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
                gate_results = run_post_dev_verification_gates(state)
                transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
                yield {
                    "agent": "verification_layer",
                    "status": "completed",
                    "message": verification_layer_status_message(
                        gate_results,
                        context="after dev retry",
                    ),
                }

                _, review_func = resolve_step("review_dev", base_agent_config)
                yield {"agent": "review_dev", "status": "in_progress", "message": "Review dev (retry)"}
                yield from run_step_with_stream_progress("review_dev", review_func, state)
                yield emit_completed("review_dev", state)

                dev_retries = get_step_retries(state, "dev")
                new_dev_review = str(state.get("dev_review_output") or "")
                verdict = _qg_extract_verdict(new_dev_review)

                # Stale-review detection for dev review loop
                if verdict == "NEEDS_WORK" and prev_dev_review_text:
                    _stale_threshold = float(
                        os.getenv("SWARM_STALE_REVIEW_SIMILARITY_THRESHOLD", "0.85").strip()
                    )
                    from difflib import SequenceMatcher
                    similarity = SequenceMatcher(None, prev_dev_review_text, new_dev_review).ratio()
                    if similarity > _stale_threshold:
                        logger.warning(
                            "Quality gate: review_dev produced near-identical review "
                            "(%.0f%% similar) — auto-approving. task_id=%s",
                            similarity * 100,
                            (state.get("task_id") or "")[:36],
                        )
                        verdict = "OK"
                prev_dev_review_text = new_dev_review

                report = _load_defect_report(state, "dev_defect_report")
                _record_open_defects(state, report)
                require_structured_blockers(report=report, verdict=verdict, step_id="review_dev")
                decision = _qg_should_retry(verdict, dev_retries, max_retries)

            if verdict == "NEEDS_WORK" and decision != "escalate":
                yield {
                    "agent": "orchestrator",
                    "status": "progress",
                    "message": (
                        f"Quality gate: review_dev still NEEDS_WORK after {max_retries} retries. "
                        f"Proceeding to QA. Consider human review."
                    ),
                }

            if decision == "escalate":
                if _should_block_for_human(state, "human_dev"):
                    raise HumanApprovalRequired(
                        step="review_dev",
                        detail=(
                            f"Quality gate: dev retries exhausted ({dev_retries}/{max_retries}). "
                            "Structured defects require manual intervention."
                        ),
                        partial_state={"open_defects": state.get("open_defects") or []},
                        resume_pipeline_step="human_dev",
                    )
                logger.warning(
                    "Quality gate: dev retries exhausted (%d/%d) but human_dev not in pipeline — continuing",
                    dev_retries, max_retries,
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
                gate_results = run_post_dev_verification_gates(state)
                transition_pipeline_phase(state, machine, PipelinePhase.VERIFY, source="verification_layer")
                yield {
                    "agent": "verification_layer",
                    "status": "completed",
                    "message": verification_layer_status_message(
                        gate_results,
                        context="after QA-triggered dev retry",
                    ),
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
                if _should_block_for_human(state, "human_qa"):
                    raise HumanApprovalRequired(
                        step="review_qa",
                        detail=(
                            f"Quality gate: QA retries exhausted ({qa_retries}/{max_retries}). "
                            "Structured defects require manual intervention."
                        ),
                        partial_state={"open_defects": state.get("open_defects") or []},
                        resume_pipeline_step="human_qa",
                    )
                logger.warning(
                    "Quality gate: QA retries exhausted (%d/%d) but human_qa not in pipeline — continuing",
                    qa_retries, max_retries,
                )


def finalize_pipeline_machine(state: Any, machine: PipelineMachine) -> None:
    if machine.phase in (PipelinePhase.VERIFY, PipelinePhase.QA) and not (state.get("open_defects") or []):
        transition_pipeline_phase(state, machine, PipelinePhase.DONE, source="verification_layer")
    _finalize_pipeline_metrics(state)
