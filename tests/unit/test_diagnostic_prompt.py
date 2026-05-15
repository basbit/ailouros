from __future__ import annotations

from backend.App.spec.application.diagnostic_prompt import RetryDiagnostic, format_diagnostic
from backend.App.spec.domain.ports import VerificationFinding


def _finding(msg: str = "undefined name", rule: str = "F821") -> VerificationFinding:
    return VerificationFinding(
        verifier_kind="flake8",
        severity="error",
        file_path="src/foo.py",
        line=5,
        message=msg,
        rule=rule,
    )


def test_format_diagnostic_contains_attempt_number() -> None:
    d = RetryDiagnostic(attempt=2, previous_code="x = 1", findings=(_finding(),))
    text = format_diagnostic(d)
    assert "Attempt 2" in text


def test_format_diagnostic_contains_finding_message() -> None:
    d = RetryDiagnostic(attempt=1, previous_code="pass", findings=(_finding("some error"),))
    text = format_diagnostic(d)
    assert "some error" in text


def test_format_diagnostic_contains_previous_code() -> None:
    d = RetryDiagnostic(attempt=1, previous_code="def broken():\n    pass", findings=(_finding(),))
    text = format_diagnostic(d)
    assert "def broken()" in text


def test_format_diagnostic_shows_rule() -> None:
    d = RetryDiagnostic(attempt=1, previous_code="x", findings=(_finding(rule="E501"),))
    text = format_diagnostic(d)
    assert "E501" in text


def test_format_diagnostic_shows_file_path() -> None:
    d = RetryDiagnostic(attempt=1, previous_code="x", findings=(_finding(),))
    text = format_diagnostic(d)
    assert "src/foo.py" in text


def test_format_diagnostic_no_findings_shows_placeholder() -> None:
    d = RetryDiagnostic(attempt=1, previous_code="x", findings=())
    text = format_diagnostic(d)
    assert "no structured findings" in text


def test_format_diagnostic_multiple_findings() -> None:
    findings = (
        _finding("error A", "E101"),
        _finding("error B", "E202"),
    )
    d = RetryDiagnostic(attempt=1, previous_code="code", findings=findings)
    text = format_diagnostic(d)
    assert "error A" in text
    assert "error B" in text


def test_retry_diagnostic_is_frozen() -> None:
    import pytest
    d = RetryDiagnostic(attempt=1, previous_code="x", findings=())
    with pytest.raises((AttributeError, TypeError)):
        d.attempt = 99  # type: ignore[misc]
