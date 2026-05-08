from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_MAX_PIPELINE_FILES = 200
_MAX_BYTES_PER_FILE = 5_000_000


@dataclass(frozen=True)
class RunSummary:
    task_id: str
    project: str
    scenario_id: str | None
    scenario_title: str | None
    scenario_category: str | None
    status: str | None
    overall_score: float | None
    artifacts_present: int
    artifacts_total: int
    finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_load_pipeline(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_BYTES_PER_FILE:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("observability: skipping %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _project_label_for(task_dir: Path, snapshot: dict[str, Any]) -> str:
    workspace = snapshot.get("workspace") or {}
    if isinstance(workspace, dict):
        candidate = workspace.get("workspace_root_resolved") or workspace.get("workspace_root")
        if isinstance(candidate, str) and candidate.strip():
            return Path(candidate).name or "unknown"
    return "unknown"


def _summarize(task_dir: Path, snapshot: dict[str, Any]) -> RunSummary:
    artifact_summary = snapshot.get("scenario_artifact_summary") or {}
    quality_summary = snapshot.get("scenario_quality_check_summary") or {}
    score: float | None = None
    if artifact_summary or quality_summary:
        try:
            from backend.App.orchestration.application.scenarios.scoring import (
                score_scenario_run,
            )
            score = float(score_scenario_run(snapshot).overall_score)
        except Exception as exc:
            logger.debug("observability: score failed for %s: %s", task_dir, exc)
            score = None
    finished_at: float | None
    try:
        finished_at = (task_dir / "pipeline.json").stat().st_mtime
    except OSError:
        finished_at = None
    return RunSummary(
        task_id=task_dir.name,
        project=_project_label_for(task_dir, snapshot),
        scenario_id=snapshot.get("scenario_id"),
        scenario_title=snapshot.get("scenario_title"),
        scenario_category=snapshot.get("scenario_category"),
        status=str(snapshot.get("_final_status") or snapshot.get("status") or ""),
        overall_score=score,
        artifacts_present=int(artifact_summary.get("present") or 0)
        if isinstance(artifact_summary, dict) else 0,
        artifacts_total=int(artifact_summary.get("total") or 0)
        if isinstance(artifact_summary, dict) else 0,
        finished_at=finished_at,
    )


def list_recent_runs(
    artifacts_root: Path,
    limit: int = _MAX_PIPELINE_FILES,
) -> list[RunSummary]:
    if not artifacts_root.is_dir():
        return []
    candidates: list[tuple[float, Path]] = []
    for entry in artifacts_root.iterdir():
        if not entry.is_dir():
            continue
        pipeline_path = entry / "pipeline.json"
        if not pipeline_path.is_file():
            continue
        try:
            mtime = pipeline_path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, entry))
    candidates.sort(reverse=True)
    summaries: list[RunSummary] = []
    for _mtime, task_dir in candidates[:limit]:
        snapshot = _safe_load_pipeline(task_dir / "pipeline.json")
        if snapshot is None:
            continue
        summaries.append(_summarize(task_dir, snapshot))
    return summaries


def _bucket_day(timestamp: float) -> str:
    import datetime as _datetime

    return _datetime.datetime.fromtimestamp(
        timestamp, tz=_datetime.timezone.utc,
    ).strftime("%Y-%m-%d")


def _series(summaries: list[RunSummary], days: int = 14) -> dict[str, Any]:
    import datetime as _datetime

    today = _datetime.datetime.now(tz=_datetime.timezone.utc).date()
    keys = [
        (today - _datetime.timedelta(days=offset)).isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    runs_per_day: dict[str, int] = {key: 0 for key in keys}
    score_per_day_sum: dict[str, float] = {key: 0.0 for key in keys}
    score_per_day_count: dict[str, int] = {key: 0 for key in keys}
    for summary in summaries:
        if summary.finished_at is None:
            continue
        bucket = _bucket_day(summary.finished_at)
        if bucket not in runs_per_day:
            continue
        runs_per_day[bucket] += 1
        if summary.overall_score is not None:
            score_per_day_sum[bucket] += summary.overall_score
            score_per_day_count[bucket] += 1
    avg_score_per_day = [
        (
            score_per_day_sum[key] / score_per_day_count[key]
            if score_per_day_count[key]
            else None
        )
        for key in keys
    ]
    return {
        "days": keys,
        "runs": [runs_per_day[key] for key in keys],
        "avg_score": avg_score_per_day,
    }


def aggregate(summaries: Iterable[RunSummary]) -> dict[str, Any]:
    pile = list(summaries)
    by_status: Counter[str] = Counter()
    by_scenario: Counter[str] = Counter()
    by_project: Counter[str] = Counter()
    score_total = 0.0
    score_count = 0
    artifacts_present_total = 0
    artifacts_total_total = 0
    for summary in pile:
        if summary.status:
            by_status[summary.status] += 1
        if summary.scenario_id:
            by_scenario[summary.scenario_id] += 1
        if summary.project:
            by_project[summary.project] += 1
        if summary.overall_score is not None:
            score_total += summary.overall_score
            score_count += 1
        artifacts_present_total += summary.artifacts_present
        artifacts_total_total += summary.artifacts_total
    avg_score = (score_total / score_count) if score_count else None
    return {
        "total": len(pile),
        "by_status": dict(by_status),
        "by_scenario": dict(by_scenario),
        "by_project": dict(by_project),
        "avg_overall_score": avg_score,
        "artifacts_present_total": artifacts_present_total,
        "artifacts_total_total": artifacts_total_total,
        "series": _series(pile),
    }


def summarize_path(artifacts_root: Path, limit: int = _MAX_PIPELINE_FILES) -> dict[str, Any]:
    summaries = list_recent_runs(artifacts_root, limit)
    return {
        "summaries": [summary.to_dict() for summary in summaries],
        "aggregate": aggregate(summaries),
    }
