"""Тесты для run_quality_checks и встроенных типов проверок."""

from backend.App.orchestration.application.scenarios.artifact_check import ArtifactStatus
from backend.App.orchestration.application.scenarios.quality_check_runner import (
    run_quality_checks,
    summarize_quality_results,
)
from backend.App.orchestration.domain.scenarios.quality_checks import QualityCheckSpec


def _spec(check_id: str, ctype: str, **config) -> QualityCheckSpec:
    return QualityCheckSpec(
        id=check_id,
        type=ctype,
        severity="error",
        blocking=False,
        config=config,
    )


def test_artifact_count_passes_when_min_met(tmp_path):
    status = [
        ArtifactStatus(path="a", present=True),
        ArtifactStatus(path="b", present=True),
        ArtifactStatus(path="c", present=False),
    ]
    spec = _spec("c1", "artifact_count", min=2)
    results = run_quality_checks([spec], tmp_path, status, [])
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].detail == {"present": 2, "total": 3, "min": 2}


def test_artifact_count_fails_when_min_unmet(tmp_path):
    status = [ArtifactStatus(path="a", present=False)]
    spec = _spec("c1", "artifact_count", min=2)
    results = run_quality_checks([spec], tmp_path, status, [])
    assert results[0].passed is False


def test_artifact_min_size_passes(tmp_path):
    target = tmp_path / "agents" / "x.txt"
    target.parent.mkdir()
    target.write_text("x" * 100, encoding="utf-8")
    spec = _spec("c1", "artifact_min_size", path="agents/x.txt", min_bytes=50)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True
    assert results[0].detail["size"] == 100


def test_artifact_min_size_fails_when_missing(tmp_path):
    spec = _spec("c1", "artifact_min_size", path="missing.txt", min_bytes=10)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False


def test_artifact_min_size_fails_when_too_small(tmp_path):
    target = tmp_path / "agents" / "x.txt"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    spec = _spec("c1", "artifact_min_size", path="agents/x.txt", min_bytes=10)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False


def test_agent_output_min_chars_passes(tmp_path):
    target = tmp_path / "agents" / "pm.txt"
    target.parent.mkdir()
    target.write_text("hello world this is content", encoding="utf-8")
    spec = _spec("c1", "agent_output_min_chars", agent="pm", min_chars=5)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True


def test_agent_output_min_chars_strips_whitespace(tmp_path):
    target = tmp_path / "agents" / "pm.txt"
    target.parent.mkdir()
    target.write_text("   \n\n  ", encoding="utf-8")
    spec = _spec("c1", "agent_output_min_chars", agent="pm", min_chars=1)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False


def test_agent_output_contains_case_insensitive(tmp_path):
    target = tmp_path / "agents" / "qa.txt"
    target.parent.mkdir()
    target.write_text("Test PASSED with no errors", encoding="utf-8")
    spec = _spec("c1", "agent_output_contains", agent="qa", substring="passed")
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True


def test_agent_output_contains_case_sensitive(tmp_path):
    target = tmp_path / "agents" / "qa.txt"
    target.parent.mkdir()
    target.write_text("test passed", encoding="utf-8")
    spec = _spec(
        "c1", "agent_output_contains",
        agent="qa", substring="PASSED", case_sensitive=True,
    )
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False


def test_agent_output_missing_file_fails(tmp_path):
    spec = _spec("c1", "agent_output_contains", agent="missing", substring="x")
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False


def test_no_warnings_passes_when_empty(tmp_path):
    spec = _spec("c1", "no_warnings")
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True


def test_no_warnings_fails_when_present(tmp_path):
    spec = _spec("c1", "no_warnings")
    results = run_quality_checks([spec], tmp_path, [], ["A warning"])
    assert results[0].passed is False
    assert results[0].detail == {"warnings": ["A warning"]}


def test_unknown_type_fails_with_message(tmp_path):
    spec = _spec("c1", "unknown_type")
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False
    assert "Unknown" in results[0].message


def test_summary_counts():
    from backend.App.orchestration.domain.scenarios.quality_checks import (
        QualityCheckResult,
    )
    results = [
        QualityCheckResult(
            id="a", type="x", passed=True, severity="error", blocking=False, message="",
        ),
        QualityCheckResult(
            id="b", type="x", passed=False, severity="error", blocking=True, message="",
        ),
        QualityCheckResult(
            id="c", type="x", passed=False, severity="warning", blocking=False, message="",
        ),
    ]
    summary = summarize_quality_results(results)
    assert summary == {
        "total": 3,
        "passed": 1,
        "failed": 2,
        "blocking_failed": ["b"],
    }


def test_artifact_min_size_rejects_traversal(tmp_path):
    parent_file = tmp_path.parent / "secret.txt"
    parent_file.write_text("oops" * 100, encoding="utf-8")
    try:
        spec = _spec(
            "c1", "artifact_min_size", path="../secret.txt", min_bytes=1,
        )
        results = run_quality_checks([spec], tmp_path, [], [])
    finally:
        parent_file.unlink(missing_ok=True)
    assert results[0].passed is False


def test_pipeline_step_count_passes(tmp_path):
    spec = _spec("c1", "pipeline_step_count", min=3)
    results = run_quality_checks(
        [spec], tmp_path, [], [], pipeline_steps=["a", "b", "c", "d"],
    )
    assert results[0].passed is True
    assert results[0].detail == {"count": 4, "min": 3}


def test_pipeline_step_count_fails_when_too_few(tmp_path):
    spec = _spec("c1", "pipeline_step_count", min=5)
    results = run_quality_checks(
        [spec], tmp_path, [], [], pipeline_steps=["a", "b"],
    )
    assert results[0].passed is False


def test_pipeline_step_count_handles_missing_steps_arg(tmp_path):
    spec = _spec("c1", "pipeline_step_count", min=1)
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False
    assert results[0].detail == {"count": 0, "min": 1}


def test_every_artifact_min_size_passes_when_all_above(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.txt").write_text("x" * 100, encoding="utf-8")
    (tmp_path / "agents" / "b.txt").write_text("y" * 200, encoding="utf-8")
    status = [
        ArtifactStatus(path="agents/a.txt", present=True, size=100),
        ArtifactStatus(path="agents/b.txt", present=True, size=200),
    ]
    spec = _spec("c1", "every_artifact_min_size", min_bytes=50)
    results = run_quality_checks([spec], tmp_path, status, [])
    assert results[0].passed is True


def test_every_artifact_min_size_skips_missing_artifacts(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.txt").write_text("x" * 200, encoding="utf-8")
    status = [
        ArtifactStatus(path="agents/a.txt", present=True, size=200),
        ArtifactStatus(path="missing.txt", present=False),
    ]
    spec = _spec("c1", "every_artifact_min_size", min_bytes=100)
    results = run_quality_checks([spec], tmp_path, status, [])
    assert results[0].passed is True


def test_every_artifact_min_size_fails_when_one_too_small(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.txt").write_text("x" * 200, encoding="utf-8")
    (tmp_path / "agents" / "b.txt").write_text("y", encoding="utf-8")
    status = [
        ArtifactStatus(path="agents/a.txt", present=True, size=200),
        ArtifactStatus(path="agents/b.txt", present=True, size=1),
    ]
    spec = _spec("c1", "every_artifact_min_size", min_bytes=50)
    results = run_quality_checks([spec], tmp_path, status, [])
    assert results[0].passed is False
    assert "agents/b.txt" in (results[0].detail or {}).get("below_threshold", [])


def test_agent_output_forbidden_passes_when_clean(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "qa.txt").write_text(
        "All checks complete and the system is healthy.", encoding="utf-8",
    )
    spec = _spec(
        "c1", "agent_output_forbidden",
        agent="qa", substrings=["secret_key", "TODO"],
    )
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True


def test_agent_output_forbidden_fails_with_marker(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "qa.txt").write_text(
        "TODO: handle the edge case", encoding="utf-8",
    )
    spec = _spec(
        "c1", "agent_output_forbidden",
        agent="qa", substrings=["TODO", "FIXME"],
    )
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False
    assert "TODO" in (results[0].detail or {}).get("found", [])


def test_agent_output_forbidden_case_sensitive(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "qa.txt").write_text("todo lower", encoding="utf-8")
    spec = _spec(
        "c1", "agent_output_forbidden",
        agent="qa", substrings=["TODO"], case_sensitive=True,
    )
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is True


def test_agent_output_forbidden_missing_config_fails(tmp_path):
    spec = _spec("c1", "agent_output_forbidden")
    results = run_quality_checks([spec], tmp_path, [], [])
    assert results[0].passed is False
    assert "required" in results[0].message
