from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Generator
from typing import Any, cast

from backend.App.orchestration.application.enforcement.verification_contract import is_human_gate_in_pipeline
from backend.App.orchestration.application.pipeline.ephemeral_state import (
    pop_ephemeral,
    set_ephemeral,
)
from backend.App.orchestration.application.pipeline.runners_policy import (
    analyze_code_max_files_default,
    analyze_code_min_output_chars,
    dev_lead_required_sections,
    devops_command_markers,
    intent_prefixes,
    needs_work_warning_threshold,
    planning_steps_output_keys,
    research_signal_steps,
    step_cleanup_keys,
    step_min_output,
)
from backend.App.orchestration.domain.exceptions import HumanApprovalRequired

_logger = logging.getLogger(__name__)

_CRITICAL_REVIEW_OUTPUT_KEYS: dict[str, str] = {}


def _get_critical_review_output_keys() -> dict[str, str]:
    global _CRITICAL_REVIEW_OUTPUT_KEYS
    if not _CRITICAL_REVIEW_OUTPUT_KEYS:
        from backend.App.orchestration.application.enforcement.enforcement_policy import (
            critical_review_step_to_output_key,
        )
        _CRITICAL_REVIEW_OUTPUT_KEYS = critical_review_step_to_output_key()
    return _CRITICAL_REVIEW_OUTPUT_KEYS


def _validate_dev_lead_output(output: str) -> list[str]:
    return [section for section in dev_lead_required_sections() if section not in output]


def run_devops_output_check(step_id: str, state: Any) -> None:
    if step_id != "devops":
        return
    devops_output = str(state.get("devops_output") or "")
    markers = devops_command_markers()
    if not any(marker in devops_output for marker in markers):
        raise RuntimeError(
            "DevOps did not provide executable commands — output contains prose only. "
            "The devops step must include runnable scripts, Dockerfiles, or shell commands."
        )


def run_analyze_code_quality_check(step_id: str, state: Any) -> None:
    if step_id != "analyze_code":
        return
    analyze_code_output = str(state.get("analyze_code_output") or "").strip()
    code_analysis_data = state.get("code_analysis") if isinstance(state.get("code_analysis"), dict) else {}
    file_count = int(code_analysis_data.get("file_count", 0)) if code_analysis_data else 0
    max_files = int(os.environ.get("SWARM_ANALYZE_CODE_MAX_FILES", str(analyze_code_max_files_default())))

    if not analyze_code_output or len(analyze_code_output) < analyze_code_min_output_chars():
        _logger.error(
            "P0-1b: analyze_code returned empty/near-empty output — pipeline paused. workspace_root=%s",
            state.get("workspace_root", ""),
        )
        raise HumanApprovalRequired(
            step="analyze_code",
            detail=(
                "analyze_code returned empty or near-empty output. "
                "The workspace may be misconfigured or empty. "
                "Please check workspace_root and ensure the project has source files, then retry."
            ),
            resume_pipeline_step="human_code_review",
            partial_state={"analyze_code_output": analyze_code_output},
        )

    if file_count > max_files:
        _logger.warning(
            "P0-1b: analyze_code found %d files (limit %d) — pipeline paused. workspace_root=%s",
            file_count, max_files, state.get("workspace_root", ""),
        )
        raise HumanApprovalRequired(
            step="analyze_code",
            detail=(
                f"analyze_code found {file_count} files (limit: {max_files}). "
                "The project scope is too large for a single pipeline run. "
                "Please narrow the scope: specify a subdirectory or reduce the file set, "
                "or increase SWARM_ANALYZE_CODE_MAX_FILES if this is intentional."
            ),
            resume_pipeline_step="human_code_review",
            partial_state={"analyze_code_output": analyze_code_output},
        )


def run_step_min_output_check(step_id: str, state: Any) -> Generator[dict[str, Any], None, None]:
    step_min_output_map = step_min_output()
    if step_id not in step_min_output_map:
        return
    output_key, minimum_chars = step_min_output_map[step_id]
    output_value = str(state.get(output_key) or "").strip()
    if len(output_value) < minimum_chars:
        yield {
            "agent": "orchestrator",
            "status": "warning",
            "message": (
                f"[{step_id}] output too short ({len(output_value)} chars, min {minimum_chars}). "
                "The model may lack context or capability for this role. "
                "Consider: 1) a more capable model, 2) remote_profile for this role, "
                "3) check that workspace_root is correct."
            ),
        }


def run_dev_lead_validation(
    step_id: str,
    state: Any,
    base_agent_config: dict[str, Any],
    step_executor: Any,
    step_extractor: Any,
    resolve_pipeline_step: Callable,
) -> Generator[dict[str, Any], None, None]:
    if step_id != "dev_lead":
        return
    dev_lead_output = str(state.get("dev_lead_output") or "")
    missing_sections = _validate_dev_lead_output(dev_lead_output)
    if not missing_sections:
        return

    set_ephemeral(state, "dev_lead_validation_warnings", missing_sections)
    _logger.warning("[DEV_LEAD_VALIDATION] Missing sections: %s", missing_sections)
    yield {
        "agent": "orchestrator",
        "status": "dev_lead_incomplete",
        "message": (
            f"[dev_lead] output is missing required sections: {missing_sections}. "
            "Re-prompting dev_lead with explicit instruction to include them."
        ),
    }
    set_ephemeral(state, "_dev_lead_missing_sections", missing_sections)
    try:
        _, dev_lead_func = resolve_pipeline_step("dev_lead", base_agent_config)
        yield {"agent": "dev_lead", "status": "in_progress", "message": "dev_lead (deliverables retry)"}
        yield from step_executor.run("dev_lead", dev_lead_func, state)
        yield step_extractor.emit_completed("dev_lead", state)
    except Exception as dev_lead_error:
        _logger.warning("[DEV_LEAD_VALIDATION] Retry failed: %s — continuing", dev_lead_error)
    finally:
        pop_ephemeral(state, "_dev_lead_missing_sections")

    still_missing = _validate_dev_lead_output(str(state.get("dev_lead_output") or ""))
    if still_missing:
        _logger.warning("[DEV_LEAD_VALIDATION] Still missing after retry: %s", still_missing)
        if is_human_gate_in_pipeline(state, "human_dev_lead"):
            raise HumanApprovalRequired(
                step="dev_lead",
                detail=(
                    f"dev_lead output is still missing required sections after retry: {still_missing}. "
                    "Human clarification is required before Dev can proceed."
                ),
                partial_state={"dev_lead_output": str(state.get("dev_lead_output") or "")},
                resume_pipeline_step="human_dev_lead",
            )
        yield {
            "agent": "orchestrator",
            "status": "dev_lead_incomplete",
            "message": (
                f"[dev_lead] still missing {still_missing} after retry — "
                "continuing but Dev step may produce incomplete code."
            ),
        }


def run_step_cleanup(step_id: str, state: Any) -> None:
    keys_to_clear = step_cleanup_keys().get(step_id, [])
    if not keys_to_clear:
        return
    for key in keys_to_clear:
        if key in state and isinstance(state.get(key), str):
            set_ephemeral(state, key, "")
    _logger.debug("CTX-2 cleanup after %s: cleared %d keys", step_id, len(keys_to_clear))


def run_state_size_monitoring(step_id: str, state: Any) -> None:
    try:
        state_size = len(json.dumps(
            {k: v for k, v in state.items() if not str(k).startswith("_")},
            ensure_ascii=False, default=str,
        ))
        if state_size > 500_000:
            _logger.error(
                "State size CRITICAL after %s: %d chars (~%dK tokens). Context overflow likely on next step.",
                step_id, state_size, state_size // 4000,
            )
        elif state_size > 200_000:
            _logger.warning(
                "State size WARNING after %s: %d chars (~%dK tokens)", step_id, state_size, state_size // 4000,
            )
    except Exception as measurement_error:
        _logger.debug("State size measurement failed after %s: %s", step_id, measurement_error)


def run_planning_output_quality_check(step_id: str, state: Any) -> Generator[dict[str, Any], None, None]:
    planning_output_keys_map = planning_steps_output_keys()
    if step_id not in planning_output_keys_map:
        return
    output_key = planning_output_keys_map[step_id]
    output_value = str(state.get(output_key) or "")
    first_line = output_value.strip().lower().split("\n")[0]
    if len(output_value.strip()) < 300 or any(first_line.startswith(prefix) for prefix in intent_prefixes()):
        yield {
            "agent": "orchestrator",
            "status": "warning",
            "message": (
                f"[{step_id}] returned empty or intent-only output ({len(output_value.strip())} chars). "
                "The model may be attempting MCP tool calls without support. "
                "Consider: 1) enabling SWARM_MCP_TOOL_CALL_FALLBACK=1, "
                "2) selecting a different model for this role."
            ),
        }
        set_ephemeral(state, "mcp_tool_call_suspected_failure", True)


def run_research_signals_extraction(step_id: str, state: Any) -> None:
    research_signal_steps_map = research_signal_steps()
    if step_id not in research_signal_steps_map:
        return
    try:
        from backend.App.orchestration.domain.research_signals import (
            build_research_advisory,
            extract_research_signals,
        )
        signal_text = str(state.get(research_signal_steps_map[step_id]) or "")
        research_advisory = build_research_advisory(signal_text)
        if research_advisory:
            set_ephemeral(state, "research_advisory", research_advisory)
            set_ephemeral(state, "research_signals", extract_research_signals(signal_text))
            _logger.info(
                "[RESEARCH_SIGNALS] Research advisory generated after %s", step_id,
            )
    except Exception as research_error:
        _logger.debug("Research signals computation failed after %s: %s", step_id, research_error)


def run_needs_work_count_check(step_id: str, state: Any) -> Generator[dict[str, Any], None, None]:
    critical_review_keys = _get_critical_review_output_keys()
    if step_id not in critical_review_keys:
        return
    from backend.App.orchestration.application.routing.pipeline_graph import _extract_verdict
    review_text = str(state.get(critical_review_keys[step_id]) or "")
    if _extract_verdict(review_text) != "NEEDS_WORK":
        return
    needs_work_count = int(cast(Any, state.get("_needs_work_count")) or 0) + 1
    set_ephemeral(state, "_needs_work_count", needs_work_count)
    if needs_work_count >= needs_work_warning_threshold():
        yield {
            "agent": "orchestrator",
            "status": "warning",
            "message": (
                f"{needs_work_count} critical reviewers returned NEEDS_WORK "
                f"(latest: {step_id}) — upstream agents may have produced "
                "empty or trivial output. "
                "Check model capabilities and whether clarify_input questions were answered."
            ),
        }


def run_all_post_step_quality_checks(
    step_id: str,
    state: Any,
    base_agent_config: dict[str, Any],
    step_executor: Any,
    step_extractor: Any,
    resolve_pipeline_step: Callable,
) -> Generator[dict[str, Any], None, bool]:
    run_devops_output_check(step_id, state)
    run_analyze_code_quality_check(step_id, state)
    yield from run_step_min_output_check(step_id, state)
    yield from run_dev_lead_validation(step_id, state, base_agent_config, step_executor, step_extractor, resolve_pipeline_step)
    run_step_cleanup(step_id, state)
    run_state_size_monitoring(step_id, state)
    yield from run_planning_output_quality_check(step_id, state)
    run_research_signals_extraction(step_id, state)
    yield from run_needs_work_count_check(step_id, state)

    if state.get("_pipeline_stop_early"):
        stop_reason = state.get("_pipeline_stop_reason") or ""
        if stop_reason:
            yield {"agent": step_id, "status": "warning", "message": stop_reason}
        return True
    return False
