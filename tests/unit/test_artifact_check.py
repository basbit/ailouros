"""Тесты для check_scenario_artifacts."""

from pathlib import Path

from backend.App.orchestration.application.scenarios.artifact_check import (
    ArtifactStatus,
    check_scenario_artifacts,
    summarize_artifact_status,
)


def test_present_file_marked_with_size_and_mtime(tmp_path: Path) -> None:
    target = tmp_path / "agents" / "pm.txt"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    result = check_scenario_artifacts(["agents/pm.txt"], tmp_path)
    assert len(result) == 1
    assert result[0].path == "agents/pm.txt"
    assert result[0].present is True
    assert result[0].size == 5
    assert result[0].mtime is not None


def test_missing_file_marked_absent(tmp_path: Path) -> None:
    result = check_scenario_artifacts(["pipeline.json"], tmp_path)
    assert len(result) == 1
    assert result[0].present is False
    assert result[0].size is None
    assert result[0].mtime is None


def test_directory_does_not_count_as_file(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    result = check_scenario_artifacts(["agents"], tmp_path)
    assert result[0].present is False


def test_empty_and_blank_entries_skipped(tmp_path: Path) -> None:
    result = check_scenario_artifacts(["", "  ", "pipeline.json"], tmp_path)
    assert len(result) == 1
    assert result[0].path == "pipeline.json"


def test_path_traversal_treated_as_missing(tmp_path: Path, caplog) -> None:
    parent_file = tmp_path.parent / "secret.txt"
    parent_file.write_text("oops", encoding="utf-8")
    try:
        result = check_scenario_artifacts(["../secret.txt"], tmp_path)
    finally:
        parent_file.unlink(missing_ok=True)
    assert len(result) == 1
    assert result[0].present is False


def test_absolute_path_treated_as_missing(tmp_path: Path) -> None:
    result = check_scenario_artifacts(["/etc/passwd"], tmp_path)
    assert result[0].present is False


def test_summarize_counts_present_and_missing() -> None:
    status = [
        ArtifactStatus(path="a", present=True, size=1, mtime=1.0),
        ArtifactStatus(path="b", present=False),
        ArtifactStatus(path="c", present=True, size=2, mtime=2.0),
    ]
    summary = summarize_artifact_status(status)
    assert summary == {"present": 2, "missing": 1, "total": 3}


def test_artifact_status_to_dict_roundtrip() -> None:
    status = ArtifactStatus(path="x", present=True, size=10, mtime=1.5)
    out = status.to_dict()
    assert out == {"path": "x", "present": True, "size": 10, "mtime": 1.5}
