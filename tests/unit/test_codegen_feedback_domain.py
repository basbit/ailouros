from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.App.integrations.domain.codegen_feedback import (
    CodegenFeedback,
    new_feedback_id,
    parse_feedback,
    serialise_feedback,
)


def _sample() -> CodegenFeedback:
    return CodegenFeedback(
        id="abc-123",
        spec_id="auth/login",
        agent="coder",
        target_file="src/auth/login.py",
        verdict="accept",
        user_edit_diff=None,
        reason="looks good",
        recorded_at=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=("auth/login", "accept"),
    )


def test_round_trip_accept():
    fb = _sample()
    raw = serialise_feedback(fb)
    restored = parse_feedback(raw)
    assert restored == fb


def test_round_trip_reject():
    fb = CodegenFeedback(
        id="x",
        spec_id="s",
        agent="a",
        target_file="f.py",
        verdict="reject",
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        tags=(),
    )
    raw = serialise_feedback(fb)
    restored = parse_feedback(raw)
    assert restored.verdict == "reject"
    assert restored.reason is None


def test_round_trip_edit_with_diff():
    fb = CodegenFeedback(
        id="y",
        spec_id="s",
        agent="a",
        target_file="f.py",
        verdict="edit",
        user_edit_diff="@@ -1 +1 @@\n-old\n+new",
        reason="minor tweak",
        recorded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        tags=("edit",),
    )
    raw = serialise_feedback(fb)
    restored = parse_feedback(raw)
    assert restored.verdict == "edit"
    assert restored.user_edit_diff == "@@ -1 +1 @@\n-old\n+new"
    assert restored.reason == "minor tweak"


def test_round_trip_preserves_tags():
    fb = CodegenFeedback(
        id="z",
        spec_id="s",
        agent="a",
        target_file="f.py",
        verdict="accept",
        user_edit_diff=None,
        reason=None,
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tags=("foo", "bar", "baz"),
    )
    raw = serialise_feedback(fb)
    restored = parse_feedback(raw)
    assert restored.tags == ("foo", "bar", "baz")


def test_round_trip_naive_datetime_gets_utc():
    raw = {
        "id": "id1",
        "spec_id": "s",
        "agent": "a",
        "target_file": "f.py",
        "verdict": "accept",
        "recorded_at": "2026-05-01T10:00:00",
    }
    fb = parse_feedback(raw)
    assert fb.recorded_at.tzinfo is not None


def test_missing_field_raises():
    raw = {
        "id": "id1",
        "spec_id": "s",
        "agent": "a",
        "verdict": "accept",
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(ValueError, match="missing required fields"):
        parse_feedback(raw)


def test_invalid_verdict_raises():
    raw = {
        "id": "id1",
        "spec_id": "s",
        "agent": "a",
        "target_file": "f.py",
        "verdict": "maybe",
        "recorded_at": "2026-01-01T00:00:00Z",
    }
    with pytest.raises(ValueError, match="invalid verdict"):
        parse_feedback(raw)


def test_new_feedback_id_is_unique():
    ids = {new_feedback_id() for _ in range(10)}
    assert len(ids) == 10


def test_serialise_tags_are_list():
    fb = _sample()
    raw = serialise_feedback(fb)
    assert isinstance(raw["tags"], list)


def test_serialise_recorded_at_is_isoformat():
    fb = _sample()
    raw = serialise_feedback(fb)
    assert "T" in raw["recorded_at"]
