"""Тесты агрегатора observability — series + базовый aggregate."""

import datetime as _dt
import json
from pathlib import Path

import pytest

from backend.App.integrations.application.observability_aggregator import (
    aggregate,
    list_recent_runs,
    summarize_path,
)


def _write_pipeline(
    artifacts_root: Path,
    task_id: str,
    *,
    scenario_id: str,
    status: str,
    score_present: bool,
    days_ago: int,
) -> None:
    task_dir = artifacts_root / task_id
    task_dir.mkdir(parents=True)
    payload = {
        "scenario_id": scenario_id,
        "scenario_title": scenario_id.replace("_", " ").title(),
        "scenario_category": "research",
        "_final_status": status,
        "workspace": {"workspace_root_resolved": "/tmp/projects/demo"},
    }
    if score_present:
        payload["scenario_artifact_summary"] = {"present": 2, "total": 3}
        payload["scenario_quality_check_summary"] = {
            "total": 4,
            "passed": 4,
            "failed": 0,
            "blocking_failed": [],
        }
    pipeline_path = task_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps(payload), encoding="utf-8")
    if days_ago > 0:
        target = _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=days_ago)
        timestamp = target.timestamp()
        import os

        os.utime(pipeline_path, (timestamp, timestamp))


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


def test_series_buckets_runs_by_day(artifacts_root: Path) -> None:
    artifacts_root.mkdir()
    _write_pipeline(
        artifacts_root, "t1", scenario_id="build_feature",
        status="completed", score_present=True, days_ago=0,
    )
    _write_pipeline(
        artifacts_root, "t2", scenario_id="build_feature",
        status="failed", score_present=False, days_ago=1,
    )
    _write_pipeline(
        artifacts_root, "t3", scenario_id="research_brief",
        status="completed", score_present=True, days_ago=2,
    )
    summaries = list_recent_runs(artifacts_root)
    payload = aggregate(summaries)
    series = payload.get("series")
    assert isinstance(series, dict)
    assert len(series["days"]) == 14
    assert sum(series["runs"]) == 3
    assert series["runs"][-1] == 1
    assert series["runs"][-2] == 1
    assert series["runs"][-3] == 1


def test_summarize_path_includes_series(artifacts_root: Path) -> None:
    artifacts_root.mkdir()
    _write_pipeline(
        artifacts_root, "t1", scenario_id="code_review",
        status="completed", score_present=True, days_ago=0,
    )
    payload = summarize_path(artifacts_root)
    assert payload["aggregate"]["total"] == 1
    assert "series" in payload["aggregate"]
    assert payload["aggregate"]["series"]["runs"][-1] == 1


def test_summary_includes_finished_at(artifacts_root: Path) -> None:
    artifacts_root.mkdir()
    _write_pipeline(
        artifacts_root, "t1", scenario_id="data_analysis",
        status="completed", score_present=False, days_ago=0,
    )
    summaries = list_recent_runs(artifacts_root)
    assert len(summaries) == 1
    assert summaries[0].finished_at is not None
