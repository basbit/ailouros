
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict, cast

from backend.App.orchestration.application.pipeline.ephemeral_state import set_ephemeral
from backend.App.orchestration.application.pipeline.pipeline_state import PipelineState
from backend.App.orchestration.domain.defect import Defect, DefectReport, cluster_defects


class PlanningReviewBlocker(TypedDict):
    review_step: str
    target_step: str
    verdict: str
    review_output: str


class DeliverableProducer(TypedDict):
    subtask_id: str
    title: str
    expected_paths: list[str]
    produced_paths: list[str]


class DeliverableWriteMappingEntry(TypedDict):
    required_path: str
    matched: bool
    producers: list[DeliverableProducer]


class DefectClusterEntry(TypedDict):
    cluster_key: str
    category: str
    count: int
    severity: str
    defect_ids: list[str]
    titles: list[str]
    file_paths: list[str]


class PipelineMetricsArtifact(TypedDict):
    task_id: str
    pipeline_phase: str
    stopped_on_verification_gate: bool
    failed_verification_gates: list[str]
    verification_gate_count: int
    p0_defect_count: int
    p1_defect_count: int
    open_defect_count: int
    defect_cluster_count: int
    spec_repo_missing_count: int
    placeholder_defect_count: int
    destructive_change_without_tests_count: int
    model_discovery_metrics: dict[str, Any]
    median_small_step_input_tokens: int
    total_input_tokens: int
    total_fix_cycles: int
    avg_dev_writes_per_minute: float
    avg_dev_artifact_yield_per_subtask: float
    file_read_cache_hits: int
    file_read_cache_misses: int
    file_read_cache_hit_rate: float
    step_metrics: dict[str, Any]


_PLANNING_REVIEW_TARGET_STEP: dict[str, str] = {
    "review_pm": "pm",
    "review_ba": "ba",
    "review_stack": "architect",
    "review_arch": "architect",
    "review_dev_lead": "dev_lead",
    "review_pm_tasks": "dev_lead",
    "review_devops": "devops",
}


def build_pipeline_metrics(state: PipelineState) -> PipelineMetricsArtifact:
    from backend.App.integrations.infrastructure.model_discovery import discovery_metrics_snapshot
    from backend.App.integrations.infrastructure.observability.step_metrics import snapshot_for_task

    task_id = str(state.get("task_id") or "").strip()
    step_metrics = snapshot_for_task(task_id)
    verification_gates = list(state.get("verification_gates") or [])
    open_defects = list(state.get("open_defects") or [])
    clustered_open_defects = list(state.get("clustered_open_defects") or [])
    dev_subtask_contracts = list(state.get("dev_subtask_contracts") or [])
    steps_raw = step_metrics.get("steps") or []

    total_input_tokens = 0
    total_cache_hits = 0
    total_cache_misses = 0
    small_step_token_samples: list[int] = []
    small_steps = ("pm", "ba", "review_pm", "review_ba", "review_dev", "review_qa", "qa", "devops")
    for step_row in steps_raw:
        if not isinstance(step_row, dict):
            continue
        step_id = str(step_row.get("step_id") or "")
        tokens = step_row.get("tokens") or {}
        input_tokens = int(tokens.get("input_tokens") or step_row.get("input_tokens") or 0)
        total_input_tokens += input_tokens
        total_cache_hits += int(tokens.get("file_read_cache_hits") or 0)
        total_cache_misses += int(tokens.get("file_read_cache_misses") or 0)
        if step_id in small_steps and input_tokens > 0:
            for _ in range(int(step_row.get("count") or 0) or 1):
                small_step_token_samples.append(input_tokens)
    small_step_token_samples.sort()
    median_small_input_tokens = (
        small_step_token_samples[len(small_step_token_samples) // 2]
        if small_step_token_samples else 0
    )

    gate_failures = [item for item in verification_gates if not item.get("passed")]
    return {
        "task_id": task_id,
        "pipeline_phase": str(state.get("pipeline_phase") or ""),
        "stopped_on_verification_gate": bool(gate_failures),
        "failed_verification_gates": [str(item.get("gate_name") or "") for item in gate_failures],
        "verification_gate_count": len(verification_gates),
        "p0_defect_count": sum(1 for item in open_defects if str(item.get("severity") or "") == "P0"),
        "p1_defect_count": sum(1 for item in open_defects if str(item.get("severity") or "") == "P1"),
        "open_defect_count": len(open_defects),
        "defect_cluster_count": len(clustered_open_defects),
        "spec_repo_missing_count": sum(
            1
            for item in verification_gates
            if str(item.get("gate_name") or "") == "spec_gate"
            for err in list(item.get("errors") or [])
            if "missing required file" in str(err.get("error") or "").lower()
        ),
        "placeholder_defect_count": sum(
            1
            for item in verification_gates
            if str(item.get("gate_name") or "") == "stub_gate"
            for _ in list(item.get("errors") or [])
        ),
        "destructive_change_without_tests_count": sum(
            1
            for item in verification_gates
            if str(item.get("gate_name") or "") == "diff_risk_gate"
            for _ in list(item.get("errors") or [])
        ),
        "model_discovery_metrics": discovery_metrics_snapshot(),
        "median_small_step_input_tokens": median_small_input_tokens,
        "total_input_tokens": total_input_tokens,
        "total_fix_cycles": int((state.get("pipeline_machine") or {}).get("fix_cycles") or 0),
        "avg_dev_writes_per_minute": round(
            sum(float(item.get("writes_per_minute") or 0.0) for item in dev_subtask_contracts)
            / max(1, len(dev_subtask_contracts)),
            3,
        ) if dev_subtask_contracts else 0.0,
        "avg_dev_artifact_yield_per_subtask": round(
            sum(float(item.get("artifact_yield_per_subtask") or 0.0) for item in dev_subtask_contracts)
            / max(1, len(dev_subtask_contracts)),
            3,
        ) if dev_subtask_contracts else 0.0,
        "file_read_cache_hits": total_cache_hits,
        "file_read_cache_misses": total_cache_misses,
        "file_read_cache_hit_rate": round(
            total_cache_hits / (total_cache_hits + total_cache_misses),
            3,
        ) if (total_cache_hits + total_cache_misses) else 0.0,
        "step_metrics": step_metrics,
    }


def finalize_pipeline_metrics(state: PipelineState) -> None:
    state["pipeline_metrics"] = cast(dict[str, Any], build_pipeline_metrics(state))


def finalize_metrics_best_effort(state: PipelineState) -> None:
    import logging as _logging
    _local_logger = _logging.getLogger(__name__)
    try:
        finalize_pipeline_metrics(state)
    except Exception as finalize_metrics_error:
        _local_logger.warning(
            "finalize_metrics_best_effort: pipeline_metrics not written — %s. "
            "This usually happens when state is partially populated during an exception path.",
            finalize_metrics_error,
        )


def load_defect_report(state: Mapping[str, Any], key: str) -> DefectReport:
    raw = state.get(key)
    if isinstance(raw, dict):
        return DefectReport.from_dict(raw)
    return DefectReport()


def merge_defect_reports(*reports: DefectReport) -> DefectReport:
    merged = DefectReport()
    for report in reports:
        merged.merge(report)
    return merged


def record_planning_review_blocker(
    state: PipelineState,
    *,
    step_id: str,
    review_output: str,
) -> None:
    from backend.App.orchestration.domain.quality_gate_policy import extract_verdict

    verdict = extract_verdict(review_output or "")
    blockers = [
        item
        for item in list(state.get("planning_review_blockers") or [])
        if str(item.get("review_step") or "") != step_id
    ]
    feedback = dict(state.get("planning_review_feedback") or {})
    target_step = _PLANNING_REVIEW_TARGET_STEP.get(step_id, "")
    if verdict == "NEEDS_WORK":
        blockers.append(
            cast(dict[str, Any], PlanningReviewBlocker(
                review_step=step_id,
                target_step=target_step,
                verdict=verdict,
                review_output=review_output,
            ))
        )
        if target_step:
            feedback[target_step] = review_output
    elif target_step:
        feedback.pop(target_step, None)
    state["planning_review_blockers"] = blockers
    state["planning_review_feedback"] = feedback


def deliverable_write_mapping(state: PipelineState) -> list[DeliverableWriteMappingEntry]:
    mapping: list[DeliverableWriteMappingEntry] = []
    subtask_contracts = list(state.get("dev_subtask_contracts") or [])
    for required_path in list(state.get("must_exist_files") or []):
        producers: list[DeliverableProducer] = []
        for contract in subtask_contracts:
            if not isinstance(contract, dict):
                continue
            produced_paths = list(contract.get("produced_paths") or [])
            expected_paths = list(contract.get("expected_paths") or [])
            if required_path in produced_paths or required_path in expected_paths:
                producers.append(
                    DeliverableProducer(
                        subtask_id=str(contract.get("subtask_id") or ""),
                        title=str(contract.get("title") or ""),
                        expected_paths=expected_paths,
                        produced_paths=produced_paths,
                    )
                )
        mapping.append(
            DeliverableWriteMappingEntry(
                required_path=required_path,
                matched=bool(producers),
                producers=producers,
            )
        )
    return mapping


def record_open_defects(state: PipelineState, *reports: DefectReport) -> None:
    open_defects = []
    grouped_defects: list[Defect] = []
    for report in reports:
        for defect in report.open_p0 + report.open_p1:
            open_defects.append(defect.to_dict())
            grouped_defects.append(defect)
    set_ephemeral(state, "open_defects", open_defects)
    clusters: list[DefectClusterEntry] = []
    for category, defects in cluster_defects(grouped_defects).items():
        unique_files: list[str] = []
        for defect in defects:
            for path in defect.file_paths:
                if path not in unique_files:
                    unique_files.append(path)
        clusters.append(
            DefectClusterEntry(
                cluster_key=category,
                category=category,
                count=len(defects),
                severity=max((defect.severity.value for defect in defects), default="P1"),
                defect_ids=[defect.id for defect in defects],
                titles=[defect.title for defect in defects if defect.title],
                file_paths=unique_files,
            )
        )
    set_ephemeral(state, "clustered_open_defects", clusters)
