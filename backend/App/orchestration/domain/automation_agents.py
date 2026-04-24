from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AutomationAgentFinding:
    agent: str
    status: str
    evidence: list[str]
    safe_tasks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "evidence": self.evidence,
            "safe_tasks": self.safe_tasks,
        }


def build_automation_agent_report(pipeline_snapshot: dict[str, Any]) -> dict[str, Any]:
    _workspace_truth = pipeline_snapshot.get("workspace_truth")
    workspace_truth: dict[str, Any] = _workspace_truth if isinstance(_workspace_truth, dict) else {}
    _run_manifest = pipeline_snapshot.get("run_manifest")
    run_manifest: dict[str, Any] = _run_manifest if isinstance(_run_manifest, dict) else {}
    _verification_gates = pipeline_snapshot.get("verification_gates")
    verification_gates: list[Any] = _verification_gates if isinstance(_verification_gates, list) else []
    _asset_manifest = pipeline_snapshot.get("asset_manifest")
    asset_manifest: dict[str, Any] = _asset_manifest if isinstance(_asset_manifest, dict) else {}

    changed_files = [str(path) for path in workspace_truth.get("changed_files") or []]
    failed_gates = [
        str(gate.get("gate_name") or "unknown")
        for gate in verification_gates
        if isinstance(gate, dict) and not gate.get("passed")
    ]
    requested_assets_raw = asset_manifest.get("requested_assets") if isinstance(asset_manifest, dict) else []
    requested_assets: list[Any] = requested_assets_raw if isinstance(requested_assets_raw, list) else []

    findings = [
        AutomationAgentFinding(
            agent="pipeline_recommender",
            status="ready" if changed_files else "needs_input",
            evidence=changed_files[:20],
            safe_tasks=["propose_pipeline_preset"] if changed_files else [],
        ),
        AutomationAgentFinding(
            agent="swarm_optimizer",
            status="needs_attention" if failed_gates else "ready",
            evidence=failed_gates,
            safe_tasks=["open_gate_review"] if failed_gates else ["record_successful_gate_profile"],
        ),
        AutomationAgentFinding(
            agent="prompt_quality_monitor",
            status="needs_attention" if pipeline_snapshot.get("verification_gate_warnings") else "ready",
            evidence=[str(pipeline_snapshot.get("verification_gate_warnings") or "")][:1],
            safe_tasks=["draft_prompt_contract_patch"] if pipeline_snapshot.get("verification_gate_warnings") else [],
        ),
        AutomationAgentFinding(
            agent="safe_task_executor",
            status="blocked" if requested_assets else "ready",
            evidence=[str(item.get("path") or "") for item in requested_assets if isinstance(item, dict)],
            safe_tasks=["write_post_run_reports"],
        ),
    ]
    return {
        "schema": "swarm_automation_agents/v1",
        "run_status": str(run_manifest.get("status") or "unknown"),
        "findings": [finding.to_dict() for finding in findings],
    }
