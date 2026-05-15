from __future__ import annotations

from backend.App.orchestration.application.pipeline.clarification_hook import (
    maybe_pause_for_clarification,
)


def test_no_pause_when_marker_missing():
    assert maybe_pause_for_clarification("ba", "plain output", {}) is None


def test_pause_when_questions_block_present():
    output = (
        "NEEDS_CLARIFICATION\n"
        "Questions for the user:\n"
        "1. Which database should be used?\n"
        "2. Should auth be optional?\n"
    )
    result = maybe_pause_for_clarification("ba", output, {})
    assert result is not None
    assert result["step_id"] == "ba"
    assert result["reason"] == "needs_clarification"
    assert len(result["questions"]) == 2
    assert "database" in result["questions"][0]["text"].lower()


def test_media_roles_default_off():
    output = "NEEDS_CLARIFICATION\nQuestions for the user:\n1. Style?\n"
    assert maybe_pause_for_clarification("image_generator", output, {}) is None


def test_explicit_role_flag_overrides_default():
    output = "NEEDS_CLARIFICATION\nQuestions for the user:\n1. Style?\n"
    result = maybe_pause_for_clarification(
        "image_generator", output, {}, role_cfg={"can_request_clarification": True}
    )
    assert result is not None


def test_explicit_disable_for_text_role():
    output = "NEEDS_CLARIFICATION\nQuestions for the user:\n1. Anything?\n"
    result = maybe_pause_for_clarification(
        "ba", output, {}, role_cfg={"can_request_clarification": False}
    )
    assert result is None


def test_no_pause_when_marker_alone_without_questions():
    output = "NEEDS_CLARIFICATION but nothing actionable"
    assert maybe_pause_for_clarification("ba", output, {}) is None
