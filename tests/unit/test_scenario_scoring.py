"""Тесты для score_scenario_run."""

from backend.App.orchestration.application.scenarios.scoring import score_scenario_run


def test_perfect_run_scores_one():
    snapshot = {
        "scenario_artifact_summary": {"present": 5, "missing": 0, "total": 5},
        "scenario_quality_check_summary": {
            "total": 3, "passed": 3, "failed": 0, "blocking_failed": [],
        },
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    assert score.artifact_score == 1.0
    assert score.quality_check_score == 1.0
    assert score.warnings_score == 1.0
    assert score.overall_score == 1.0


def test_partial_artifacts_lowers_artifact_score():
    snapshot = {
        "scenario_artifact_summary": {"present": 2, "missing": 3, "total": 5},
        "scenario_quality_check_summary": {"total": 0, "passed": 0},
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    assert score.artifact_score == 0.4


def test_failed_quality_checks_lower_quality_score():
    snapshot = {
        "scenario_artifact_summary": {"present": 0, "missing": 0, "total": 0},
        "scenario_quality_check_summary": {"total": 4, "passed": 1, "failed": 3},
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    assert score.quality_check_score == 0.25


def test_warnings_decrease_warnings_score():
    snapshot = {
        "scenario_artifact_summary": {"present": 0, "missing": 0, "total": 0},
        "scenario_quality_check_summary": {"total": 0, "passed": 0},
        "scenario_warnings": ["a", "b"],
    }
    score = score_scenario_run(snapshot)
    assert abs(score.warnings_score - 0.6) < 1e-9


def test_warnings_score_floors_at_zero():
    snapshot = {
        "scenario_artifact_summary": {},
        "scenario_quality_check_summary": {},
        "scenario_warnings": ["a", "b", "c", "d", "e", "f"],
    }
    score = score_scenario_run(snapshot)
    assert score.warnings_score == 0.0


def test_empty_categories_default_to_one():
    snapshot = {}
    score = score_scenario_run(snapshot)
    assert score.artifact_score == 1.0
    assert score.quality_check_score == 1.0
    assert score.warnings_score == 1.0
    assert score.overall_score == 1.0


def test_overall_uses_default_weights():
    snapshot = {
        "scenario_artifact_summary": {"present": 1, "total": 2},
        "scenario_quality_check_summary": {"passed": 0, "total": 1},
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    expected = 0.5 * 0.4 + 0.0 * 0.5 + 1.0 * 0.1
    assert abs(score.overall_score - expected) < 1e-9


def test_custom_weights_normalized():
    snapshot = {
        "scenario_artifact_summary": {"present": 0, "total": 1},
        "scenario_quality_check_summary": {"passed": 1, "total": 1},
        "scenario_warnings": [],
    }
    score = score_scenario_run(
        snapshot,
        weights={"artifacts": 1, "quality_checks": 1, "warnings": 0},
    )
    assert abs(score.overall_score - 0.5) < 1e-9


def test_breakdown_includes_blocking_failed():
    snapshot = {
        "scenario_artifact_summary": {"present": 1, "total": 1},
        "scenario_quality_check_summary": {
            "passed": 1, "total": 2, "blocking_failed": ["core_artifacts_present"],
        },
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    assert score.breakdown["quality_checks"]["blocking_failed"] == [
        "core_artifacts_present",
    ]


def test_invalid_weights_fall_back_to_default():
    snapshot = {
        "scenario_artifact_summary": {"present": 1, "total": 1},
        "scenario_quality_check_summary": {"passed": 1, "total": 1},
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot, weights={"artifacts": -5})
    assert score.overall_score == 1.0


def test_score_to_dict_roundtrips():
    snapshot = {
        "scenario_artifact_summary": {"present": 1, "total": 1},
        "scenario_quality_check_summary": {"passed": 1, "total": 1},
        "scenario_warnings": [],
    }
    score = score_scenario_run(snapshot)
    payload = score.to_dict()
    assert "overall_score" in payload
    assert "breakdown" in payload
    assert "weights" in payload["breakdown"]
