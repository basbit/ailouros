from __future__ import annotations

from backend.App.orchestration.application.routing.step_order_analyzer import (
    analyze_pipeline_step_order,
)


def test_canonical_order_has_no_violations() -> None:
    canonical = [
        "clarify_input",
        "pm",
        "review_pm",
        "architect",
        "review_arch",
        "devops",
        "review_devops",
        "dev_lead",
        "review_dev_lead",
        "dev",
        "review_dev",
        "qa",
        "review_qa",
    ]

    report = analyze_pipeline_step_order(canonical)

    assert not report.has_violations
    assert report.violations == ()


def test_review_before_target_is_a_violation() -> None:
    steps = ["pm", "review_dev_lead", "dev", "dev_lead"]

    report = analyze_pipeline_step_order(steps)

    assert report.has_violations
    violation_pairs = {
        (violation.step_id, violation.missing_prerequisite)
        for violation in report.violations
    }
    assert ("review_dev_lead", "dev_lead") in violation_pairs
    assert ("dev", "dev_lead") in violation_pairs


def test_missing_prerequisite_is_not_a_violation() -> None:
    steps = ["clarify_input", "review_pm"]

    report = analyze_pipeline_step_order(steps)

    assert not report.has_violations


def test_artifact_1fa3b6ad_user_pipeline_is_flagged() -> None:
    steps = [
        "clarify_input",
        "pm",
        "architect",
        "devops",
        "review_dev_lead",
        "dev",
        "dev_lead",
        "review_devops",
        "qa",
        "review_qa",
        "review_dev",
        "asset_fetcher",
        "ui_designer",
    ]

    report = analyze_pipeline_step_order(steps)

    flagged_step_ids = {violation.step_id for violation in report.violations}
    assert "review_dev_lead" in flagged_step_ids
    assert "dev" in flagged_step_ids


def test_summary_lists_each_violation() -> None:
    steps = ["pm", "review_dev_lead", "dev", "dev_lead"]

    summary = analyze_pipeline_step_order(steps).format_summary()

    assert "review_dev_lead" in summary
    assert "dev_lead" in summary
