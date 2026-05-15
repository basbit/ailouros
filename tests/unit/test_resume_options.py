from __future__ import annotations

import json
from pathlib import Path

from backend.App.orchestration.application.use_cases.task_queries import (
    compute_resume_options,
)


def _write_snapshot(artifacts_root: Path, task_id: str, payload: dict) -> None:
    task_dir = artifacts_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "pipeline.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_no_snapshot_returns_not_resumable(tmp_path: Path) -> None:
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "failed"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is False
    assert result["pipeline_snapshot_present"] is False
    assert result["reason"] == "not_resumable"


def test_clarification_pause_takes_priority(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        "t1",
        {"clarification_pause": {"step_id": "ba"}, "partial_state": {"x": 1}},
    )
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "awaiting_human"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is True
    assert result["resume_step"] == "ba"
    assert result["reason"] == "clarification_pause"


def test_partial_state_with_resume_step(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        "t1",
        {"resume_from_step": "dev", "partial_state": {"foo": "bar"}},
    )
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "failed"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is True
    assert result["resume_step"] == "dev"
    assert result["reason"] == "partial_state"


def test_failed_step_when_status_failed(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "t1", {"failed_step": "dev"})
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "failed"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is True
    assert result["resume_step"] == "dev"
    assert result["reason"] == "failed_step"


def test_failed_step_blocked_when_status_completed(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "t1", {"failed_step": "dev"})
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "completed"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is False


def test_resume_step_without_partial_state(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, "t1", {"resume_from_step": "dev"})
    result = compute_resume_options(
        task_id="t1",
        task_data={"status": "failed"},
        artifacts_root=tmp_path,
    )
    assert result["can_resume"] is False
    assert result["resume_step"] == "dev"
