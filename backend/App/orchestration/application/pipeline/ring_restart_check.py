from __future__ import annotations

import logging
import os
from typing import Any, Optional

from backend.App.orchestration.application.enforcement.ring_escalation_recorder import (
    consume_ring_unresolved_escalations,
)
from backend.App.orchestration.application.pipeline.ring_topology import (
    build_ring_pass_defect_context,
)

_logger = logging.getLogger(__name__)


def ring_max_restarts_default() -> int:
    return int(os.getenv("SWARM_RING_MAX_RESTARTS", "2"))


def topology_from_agent_config(agent_config: dict[str, Any]) -> str:
    if not isinstance(agent_config, dict):
        return ""
    swarm_cfg = agent_config.get("swarm")
    if not isinstance(swarm_cfg, dict):
        return ""
    return str(swarm_cfg.get("topology") or "").strip()


def collect_failed_verification_gates(state: dict[str, Any]) -> list[str]:
    failed: list[str] = []
    for gate_record in state.get("verification_gates") or []:
        if isinstance(gate_record, dict) and not bool(gate_record.get("passed", True)):
            gate_name = str(gate_record.get("gate_name") or "").strip()
            if gate_name:
                failed.append(gate_name)
    return failed


def evaluate_ring_restart(
    state: dict[str, Any],
    topology: str,
    pipeline_steps: Optional[list[str]],
    ring_pass: int,
    ring_max_restarts: Optional[int] = None,
) -> dict[str, Any]:
    max_restarts = ring_max_restarts if ring_max_restarts is not None else ring_max_restarts_default()
    open_defects = list(state.get("open_defects") or [])
    ring_unresolved = consume_ring_unresolved_escalations(state)
    verification_warnings_text = str(state.get("verification_gate_warnings") or "").strip()
    failed_verification_gates = collect_failed_verification_gates(state)
    should_restart = (
        topology == "ring"
        and pipeline_steps is not None
        and ring_pass < max_restarts
        and bool(open_defects or ring_unresolved or verification_warnings_text or failed_verification_gates)
    )
    return {
        "should_restart": should_restart,
        "ring_pass": ring_pass,
        "ring_max_restarts": max_restarts,
        "open_defects": open_defects,
        "ring_unresolved": ring_unresolved,
        "verification_warnings_text": verification_warnings_text,
        "failed_verification_gates": failed_verification_gates,
    }


def build_ring_restart_defect_context(evaluation: dict[str, Any]) -> str:
    open_defects = evaluation.get("open_defects") or []
    ring_unresolved = evaluation.get("ring_unresolved") or []
    failed_verification_gates = evaluation.get("failed_verification_gates") or []
    verification_warnings_text = str(evaluation.get("verification_warnings_text") or "")
    ring_pass = int(evaluation.get("ring_pass") or 0)

    defect_context = build_ring_pass_defect_context(open_defects, ring_pass + 1)
    if ring_unresolved:
        escalation_lines = "\n".join(
            f"  - step={entry.get('step_id')} verdict={entry.get('verdict')} "
            f"retries={entry.get('retries')}/{entry.get('max_retries')}: {entry.get('reason', '')}"
            for entry in ring_unresolved
        )
        defect_context += (
            f"\n\n## Unresolved quality gate escalations ({len(ring_unresolved)})\n"
            f"{escalation_lines}\n"
            "These reviewers exhausted their retries without passing — address them on this ring pass.\n"
        )
    if failed_verification_gates:
        defect_context += (
            f"\n\n## Failed verification gates ({len(failed_verification_gates)})\n"
            f"  - {', '.join(failed_verification_gates)}\n"
            "These gates flagged issues in production paths — fix them on this ring pass.\n"
        )
    if verification_warnings_text:
        warnings_truncated = (
            verification_warnings_text[:1500] + "…[truncated]"
            if len(verification_warnings_text) > 1500
            else verification_warnings_text
        )
        defect_context += (
            f"\n\n## Verification gate warnings\n{warnings_truncated}\n"
            "Address these warnings on this ring pass.\n"
        )
    return defect_context


def build_ring_restart_event(evaluation: dict[str, Any]) -> dict[str, Any]:
    open_defects = evaluation.get("open_defects") or []
    ring_unresolved = evaluation.get("ring_unresolved") or []
    failed_verification_gates = evaluation.get("failed_verification_gates") or []
    verification_warnings_text = str(evaluation.get("verification_warnings_text") or "")
    ring_pass = int(evaluation.get("ring_pass") or 0)
    ring_max_restarts = int(evaluation.get("ring_max_restarts") or 0)
    total_issues = (
        len(open_defects)
        + len(ring_unresolved)
        + len(failed_verification_gates)
        + (1 if verification_warnings_text else 0)
    )
    _logger.info(
        "Ring pass %d/%d: %d defect(s), %d escalation(s), %d failed gate(s), warnings=%s — restarting",
        ring_pass + 1, ring_max_restarts,
        len(open_defects), len(ring_unresolved),
        len(failed_verification_gates), bool(verification_warnings_text),
    )
    return {
        "agent": "orchestrator",
        "status": "ring_restart",
        "message": (
            f"Ring pass {ring_pass + 1}/{ring_max_restarts}: "
            f"{len(open_defects)} open defect(s), {len(ring_unresolved)} unresolved escalation(s), "
            f"{len(failed_verification_gates)} failed gate(s) — "
            "restarting pipeline with defect context"
        ),
        "restart_pass": ring_pass + 1,
        "defect_count": total_issues,
    }
