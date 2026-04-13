"""Mixture-of-Reviewers: parallel reviewer panel + aggregation call."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, TypeVar

from backend.App.orchestration.application.agent_runner import run_agent_with_boundary
from backend.App.orchestration.application.parallel_limits import swarm_max_parallel_tasks
from backend.App.orchestration.domain.defect import parse_defect_report
from backend.App.orchestration.domain.quality_gate_policy import extract_defect_report, extract_verdict

_log = logging.getLogger(__name__)

_VERDICT_RE = re.compile(r"VERDICT\s*:", re.IGNORECASE)
_DEFECT_REPORT_RE = re.compile(r"<defect_report>.*?</defect_report>", re.IGNORECASE | re.DOTALL)

# Telemetry counters (process-level, reset on restart)
_verdict_missing_count: int = 0
_verdict_repaired_count: int = 0


def _has_verdict(text: str) -> bool:
    """Return True if text contains a VERDICT: marker."""
    return bool(_VERDICT_RE.search(text or ""))


def get_verdict_gate_metrics() -> dict[str, int]:
    """Return process-level verdict-gate telemetry counters.

    Keys:
    - ``verdict_missing_total`` / ``response_rejected_by_format_gate``: times a reviewer
      response had no VERDICT marker and was caught by the format gate.
    - ``verdict_repaired_total``: times the repair-prompt successfully added a VERDICT.
    """
    return {
        "verdict_missing_total": _verdict_missing_count,
        "response_rejected_by_format_gate": _verdict_missing_count,
        "verdict_repaired_total": _verdict_repaired_count,
    }


T = TypeVar("T")

_PARALLEL_SEM: Optional[threading.Semaphore] = None


def _parallel_semaphore() -> threading.Semaphore:
    global _PARALLEL_SEM
    if _PARALLEL_SEM is None:
        _PARALLEL_SEM = threading.Semaphore(swarm_max_parallel_tasks())
    return _PARALLEL_SEM


def _with_semaphore(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    sem = _parallel_semaphore()
    sem.acquire()
    try:
        return fn(*args, **kwargs)
    finally:
        sem.release()


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return False


def _reviewer_cfg(state: Mapping[str, Any]) -> dict[str, Any]:
    ac = state.get("agent_config")
    if not isinstance(ac, dict):
        return {}
    r = ac.get("reviewer")
    return r if isinstance(r, dict) else {}


def _moa_cfg(state: Mapping[str, Any]) -> dict[str, Any]:
    rc = _reviewer_cfg(state)
    m = rc.get("moa")
    return m if isinstance(m, dict) else {}


def moa_enabled_for_step(state: Mapping[str, Any], pipeline_step: str) -> bool:
    m = _moa_cfg(state)
    if not _truthy(m.get("enabled")):
        return False
    steps = m.get("steps")
    if isinstance(steps, list) and steps:
        allowed = {str(s).strip() for s in steps if str(s).strip()}
        return pipeline_step in allowed
    return True


def _panel_size(state: Mapping[str, Any]) -> int:
    m = _moa_cfg(state)
    _default = int(os.environ.get("SWARM_MOA_PANEL_SIZE", "3"))
    try:
        n = int(m.get("panel_size") or m.get("count") or _default)
    except (TypeError, ValueError):
        n = _default
    return max(2, min(8, n))


def _apply_verdict_gate(
    out: str,
    pipeline_step: str,
    prompt: str,
    agent_factory: Callable[[], Any],
    state: Mapping[str, Any],
) -> str:
    """Ensure reviewer output contains a VERDICT marker.

    If missing → repair-prompt once. If still missing after repair → append
    VERDICT: NEEDS_WORK to force a determinate outcome and avoid silent OK.

    Increments process-level telemetry counters ``_verdict_missing_count`` and
    ``_verdict_repaired_total``.
    """
    global _verdict_missing_count, _verdict_repaired_count
    if _has_verdict(out):
        return out
    _verdict_missing_count += 1
    _log.warning(
        "reviewer(%s): response_rejected_by_format_gate — no VERDICT marker found "
        "(len=%d). Sending repair-prompt.",
        pipeline_step,
        len(out),
    )
    _repair_prompt = (
        prompt
        + "\n\n[CRITICAL] Your previous response did not include a VERDICT line. "
        "You MUST end your response with exactly:\n"
        "VERDICT: OK\nor\nVERDICT: NEEDS_WORK\n"
        "Nothing else after the VERDICT line."
    )
    try:
        repair_agent = agent_factory()
        out = run_agent_with_boundary(state, repair_agent, _repair_prompt, step_id=pipeline_step)
    except Exception as exc:
        _log.warning("reviewer(%s): repair-prompt call failed: %s", pipeline_step, exc)
    if not _has_verdict(out):
        _log.warning(
            "reviewer(%s): VERDICT still missing after repair — appending VERDICT: NEEDS_WORK "
            "(invalid_response_shape)",
            pipeline_step,
        )
        out = out.rstrip() + "\n\nVERDICT: NEEDS_WORK"
    else:
        _verdict_repaired_count += 1
        _log.info("reviewer(%s): VERDICT gate repaired successfully", pipeline_step)
    return out


def _check_evidence_contract(pipeline_step: str, out: str) -> str:
    """Append evidence_incomplete marker if reviewer read no files.

    Reads ``_last_mcp_telemetry.files_read_count`` from the current thread —
    this is set by ``loop.py`` after every MCP tool-loop run.
    If no file-read tool was called, the reviewer verdict is flagged as
    ``evidence_incomplete`` with a WARNING log so operators can detect
    low-quality shallow reviews.

    Controlled by ``SWARM_REVIEWER_MIN_FILES_READ`` (default 1).
    Set to ``0`` to disable the check entirely.
    """
    min_reads_raw = os.environ.get("SWARM_REVIEWER_MIN_FILES_READ", "1").strip()
    try:
        min_reads = int(min_reads_raw)
    except ValueError:
        min_reads = 1
    if min_reads <= 0:
        return out
    try:
        from backend.App.integrations.infrastructure.mcp.openai_loop.loop import (
            _last_mcp_telemetry,
        )
        files_read = getattr(_last_mcp_telemetry, "files_read_count", None)
    except Exception:
        return out
    if files_read is None:
        # No telemetry available (e.g. no-MCP path) — skip check
        return out
    if files_read < min_reads:
        _log.warning(
            "reviewer(%s): evidence_incomplete — files_read=%d < min=%d "
            "(reviewer may have based verdict on directory listings only). "
            "Appending evidence_incomplete note.",
            pipeline_step,
            files_read,
            min_reads,
        )
        out = (
            out.rstrip()
            + f"\n\n[evidence_incomplete: reviewer read {files_read} file(s) "
            f"(minimum required: {min_reads}). Verdict confidence may be low.]"
        )
    return out


def _first_review_signal(text: str) -> str:
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("<defect_report>") or line.startswith("</defect_report>"):
            continue
        if line.upper().startswith("VERDICT:"):
            continue
        line = re.sub(r"^[#*\-\d\.\)\[\]\s|`>]+", "", line).strip()
        if line:
            return line[:280]
    return "Reviewer blocked the pipeline without a machine-readable defect report."


def _replace_defect_report_block(text: str, block: str) -> str:
    body = _DEFECT_REPORT_RE.sub("", text or "").rstrip()
    verdict_match = re.search(r"(^|\n)(VERDICT\s*:\s*\w+\s*)$", body, re.IGNORECASE)
    if verdict_match:
        prefix = body[:verdict_match.start()].rstrip()
        verdict_line = verdict_match.group(2).strip()
        if prefix:
            return f"{prefix}\n\n{block}\n{verdict_line}"
        return f"{block}\n{verdict_line}"
    if body:
        return f"{body}\n\n{block}"
    return block


def _synthetic_blocking_report(text: str, pipeline_step: str) -> str:
    summary = _first_review_signal(text)
    payload = {
        "defects": [
            {
                "id": f"{pipeline_step.upper()}-CONTRACT-1",
                "title": "Reviewer blocked without structured blockers",
                "severity": "P1",
                "file_paths": [],
                "expected": "A blocking review must include a valid <defect_report> JSON block with at least one P0/P1 defect.",
                "actual": summary,
                "repro_steps": [
                    "Run the reviewer for this step.",
                    "Inspect the response for a missing or non-blocking <defect_report> JSON block.",
                ],
                "acceptance": [
                    "Regenerate the review with explicit P0/P1 defects grounded in the blocking findings.",
                ],
                "category": "review_contract",
                "fixed": False,
            }
        ],
        "test_scenarios": [],
        "edge_cases": [],
        "regression_checks": [],
    }
    block = f"<defect_report>{json.dumps(payload, ensure_ascii=True)}</defect_report>"
    return _replace_defect_report_block(text, block)


def _apply_json_defect_report_gate(
    out: str,
    pipeline_step: str,
    prompt: str,
    agent_factory: Callable[[], Any],
    state: Mapping[str, Any],
) -> str:
    if extract_verdict(out) != "NEEDS_WORK":
        return out
    if parse_defect_report(out).has_blockers:
        return out

    _log.warning(
        "reviewer(%s): NEEDS_WORK without structured P0/P1 defect_report. Sending repair-prompt.",
        pipeline_step,
    )
    repair_prompt = (
        prompt
        + "\n\n[CRITICAL] Your previous response blocked the pipeline but did not include "
        "a valid `<defect_report>...</defect_report>` JSON block with at least one P0 or P1 defect.\n"
        "Rewrite the entire response and follow this exact order:\n"
        "1. Brief human-readable summary.\n"
        "2. A valid `<defect_report>...</defect_report>` JSON block.\n"
        "3. Final line `VERDICT: NEEDS_WORK`.\n"
        "The JSON must contain at least one blocking defect grounded in your own findings.\n\n"
        "Previous response:\n"
        + out
    )
    try:
        repair_agent = agent_factory()
        repaired = run_agent_with_boundary(state, repair_agent, repair_prompt, step_id=pipeline_step)
        repaired = _check_evidence_contract(pipeline_step, repaired)
        repaired = _apply_verdict_gate(repaired, pipeline_step, repair_prompt, agent_factory, state)
        if parse_defect_report(repaired).has_blockers:
            return repaired
        out = repaired
    except Exception as exc:
        _log.warning("reviewer(%s): defect_report repair call failed: %s", pipeline_step, exc)

    _log.warning(
        "reviewer(%s): defect_report still missing after repair — synthesizing fallback blocker.",
        pipeline_step,
    )
    return _synthetic_blocking_report(out, pipeline_step)


def run_reviewer_or_moa(
    state: Mapping[str, Any],
    *,
    pipeline_step: str,
    prompt: str,
    output_key: str,
    model_key: str,
    provider_key: str,
    agent_factory: Callable[[], Any],
    require_json_defect_report: bool = False,
) -> dict[str, Any]:
    if not moa_enabled_for_step(state, pipeline_step):
        agent = agent_factory()
        out = run_agent_with_boundary(state, agent, prompt, step_id=pipeline_step)
        out = _check_evidence_contract(pipeline_step, out)
        out = _apply_verdict_gate(out, pipeline_step, prompt, agent_factory, state)
        if require_json_defect_report:
            out = _apply_json_defect_report_gate(out, pipeline_step, prompt, agent_factory, state)
        return {
            output_key: out,
            model_key: agent.used_model,
            provider_key: agent.used_provider,
            "defects": extract_defect_report(out),
        }

    n = _panel_size(state)
    m = _moa_cfg(state)
    max_workers = swarm_max_parallel_tasks()
    w = min(max_workers, n)

    def _one(i: int) -> tuple[int, str]:
        ag = agent_factory()
        suffix = (
            f"\n\n[Review panel {i + 1}/{n}] Provide your independent assessment; "
            "end with VERDICT: OK or VERDICT: NEEDS_WORK."
        )
        text = run_agent_with_boundary(state, ag, prompt + suffix, step_id=pipeline_step)
        return i, text

    panels: list[str] = [""] * n
    with ThreadPoolExecutor(max_workers=w) as ex:
        futs = [ex.submit(_with_semaphore, _one, i) for i in range(n)]
        for fut in as_completed(futs):
            i, text = fut.result()
            panels[i] = text

    combined = "\n\n---\n\n".join(
        f"### Reviewer {i + 1}\n{p}" for i, p in enumerate(panels) if p
    )
    agg = agent_factory()
    agg_prompt = (
        "Below are independent assessments from the review panel. Synthesize a single final conclusion: "
        "brief summary of disagreements, final VERDICT: OK only if the majority is OK "
        "and there are no critical risks; otherwise VERDICT: NEEDS_WORK.\n\n"
        + combined
    )
    extra = str(m.get("aggregator_hint") or "").strip()
    if extra:
        agg_prompt = extra + "\n\n" + agg_prompt
    final = run_agent_with_boundary(state, agg, agg_prompt, step_id=pipeline_step)
    final = _apply_verdict_gate(final, pipeline_step, agg_prompt, agent_factory, state)
    if require_json_defect_report:
        final = _apply_json_defect_report_gate(final, pipeline_step, agg_prompt, agent_factory, state)
    return {
        output_key: f"[MoA x{n}]\n{final}\n\n--- Panel ---\n{combined}",
        model_key: agg.used_model,
        provider_key: agg.used_provider,
        "defects": extract_defect_report(final),
    }
