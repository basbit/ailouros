from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.App.integrations.domain.postmortem import (
    Postmortem,
    parse_postmortem,
    serialise_postmortem,
)


def _make_postmortem(**overrides) -> Postmortem:
    defaults = dict(
        id="abc-123",
        spec_id="auth/login",
        agent="stub",
        failure_kind="verifier_error",
        summary="flake8 found 3 errors",
        findings_excerpt=("E501 line too long",),
        recovery_attempted="1 retry attempt(s) made",
        outcome="failed",
        recorded_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc),
        tags=("auth/login", "stub", "verifier_error"),
    )
    defaults.update(overrides)
    return Postmortem(**defaults)


def test_frozen_dataclass_rejects_mutation():
    pm = _make_postmortem()
    with pytest.raises((AttributeError, TypeError)):
        pm.summary = "changed"


def test_round_trip_preserves_all_fields():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    restored = parse_postmortem(payload)
    assert restored.id == pm.id
    assert restored.spec_id == pm.spec_id
    assert restored.agent == pm.agent
    assert restored.failure_kind == pm.failure_kind
    assert restored.summary == pm.summary
    assert restored.findings_excerpt == pm.findings_excerpt
    assert restored.recovery_attempted == pm.recovery_attempted
    assert restored.outcome == pm.outcome
    assert restored.recorded_at == pm.recorded_at
    assert restored.tags == pm.tags


def test_serialise_produces_serialisable_types():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    assert isinstance(payload["id"], str)
    assert isinstance(payload["findings_excerpt"], list)
    assert isinstance(payload["tags"], list)
    assert isinstance(payload["recorded_at"], str)


def test_parse_missing_field_raises():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    del payload["summary"]
    with pytest.raises(ValueError, match="missing required fields"):
        parse_postmortem(payload)


def test_parse_invalid_failure_kind_raises():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    payload["failure_kind"] = "bad_kind"
    with pytest.raises(ValueError, match="invalid failure_kind"):
        parse_postmortem(payload)


def test_parse_invalid_outcome_raises():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    payload["outcome"] = "unknown"
    with pytest.raises(ValueError, match="invalid outcome"):
        parse_postmortem(payload)


def test_parse_naive_datetime_gets_utc():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    payload["recorded_at"] = "2026-05-15T12:00:00"
    restored = parse_postmortem(payload)
    assert restored.recorded_at.tzinfo is not None


def test_parse_datetime_object_accepted():
    pm = _make_postmortem()
    payload = serialise_postmortem(pm)
    payload["recorded_at"] = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    restored = parse_postmortem(payload)
    assert restored.recorded_at.year == 2026


def test_parse_empty_findings_excerpt():
    pm = _make_postmortem(findings_excerpt=())
    payload = serialise_postmortem(pm)
    restored = parse_postmortem(payload)
    assert restored.findings_excerpt == ()


def test_failure_kind_retry_exhausted_roundtrip():
    pm = _make_postmortem(failure_kind="retry_exhausted", outcome="failed")
    payload = serialise_postmortem(pm)
    restored = parse_postmortem(payload)
    assert restored.failure_kind == "retry_exhausted"


def test_outcome_succeeded_after_retry_roundtrip():
    pm = _make_postmortem(outcome="succeeded_after_retry")
    payload = serialise_postmortem(pm)
    restored = parse_postmortem(payload)
    assert restored.outcome == "succeeded_after_retry"
