from __future__ import annotations

from unittest.mock import MagicMock

from backend.App.integrations.application.postmortem_recorder import (
    persist_postmortem,
    record_codegen_failure,
)
from backend.App.integrations.domain.postmortem import Postmortem
from backend.App.spec.domain.ports import VerificationFinding


def _make_finding(msg: str = "E501 line too long") -> VerificationFinding:
    return VerificationFinding(
        verifier_kind="flake8",
        severity="error",
        file_path="src/auth.py",
        line=10,
        message=msg,
        rule="E501",
    )


def _make_attempt(attempt: int, count: int, msgs: list[str]):
    from backend.App.spec.application.codegen import AttemptRecord

    return AttemptRecord(
        attempt=attempt,
        finding_count=count,
        first_findings=tuple(_make_finding(m) for m in msgs),
        mode="full_file",
    )


def test_record_failure_from_retry_history():
    history = [
        _make_attempt(1, 3, ["E501 too long", "F401 unused import", "E302 blank lines"]),
        _make_attempt(2, 2, ["E501 too long", "F401 unused import"]),
    ]
    pm = record_codegen_failure("auth/login", "stub", history)
    assert isinstance(pm, Postmortem)
    assert pm.spec_id == "auth/login"
    assert pm.agent == "stub"
    assert pm.failure_kind == "retry_exhausted"
    assert pm.outcome == "failed"
    assert "2 attempt" in pm.summary or "error" in pm.summary


def test_record_failure_findings_excerpt_takes_first_three():
    msgs = ["err1", "err2", "err3"]
    history = [_make_attempt(1, 3, msgs)]
    pm = record_codegen_failure("auth/login", "stub", history)
    assert len(pm.findings_excerpt) <= 3
    assert pm.findings_excerpt[0] == "err1"


def test_record_failure_with_exception_no_history():
    exc = RuntimeError("network timeout")
    pm = record_codegen_failure("auth/login", "stub", [], final_exception=exc)
    assert pm.failure_kind == "exception"
    assert "network timeout" in pm.summary


def test_record_failure_with_exception_and_history():
    history = [_make_attempt(1, 1, ["some error"])]
    exc = RuntimeError("llm failed")
    pm = record_codegen_failure("auth/login", "stub", history, final_exception=exc)
    assert pm.failure_kind in ("retry_exhausted", "exception")
    assert pm.outcome == "failed"


def test_record_failure_empty_history_no_exception():
    pm = record_codegen_failure("auth/login", "stub", [])
    assert pm.failure_kind == "exception"
    assert pm.summary
    assert pm.outcome == "failed"


def test_record_failure_tags_include_spec_agent_kind():
    history = [_make_attempt(1, 1, ["err"])]
    pm = record_codegen_failure("auth/login", "stub", history)
    assert "auth/login" in pm.tags
    assert "stub" in pm.tags


def test_record_failure_recorded_at_is_utc():
    from datetime import timezone

    pm = record_codegen_failure("x", "y", [])
    assert pm.recorded_at.tzinfo is not None
    assert pm.recorded_at.tzinfo == timezone.utc


def test_persist_postmortem_calls_upsert():
    history = [_make_attempt(1, 1, ["err"])]
    pm = record_codegen_failure("auth/login", "stub", history)

    vector_store = MagicMock()
    embedding_provider = MagicMock()
    embedding_provider.embed.return_value = [[0.1, 0.2, 0.3]]

    persist_postmortem(pm, vector_store, embedding_provider)

    vector_store.upsert.assert_called_once()
    call_args = vector_store.upsert.call_args
    assert call_args[0][0] == "postmortems"
    assert call_args[0][1] == pm.id


def test_persist_postmortem_no_embedding_provider_still_upserts():
    history = [_make_attempt(1, 1, ["err"])]
    pm = record_codegen_failure("auth/login", "stub", history)

    vector_store = MagicMock()
    persist_postmortem(pm, vector_store, None)

    vector_store.upsert.assert_called_once()
    call_args = vector_store.upsert.call_args
    assert call_args[0][2] == []
