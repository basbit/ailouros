from __future__ import annotations

import pytest

from backend.App.spec.domain.ports import CodeVerifier, VerificationFinding


def _make_finding(**kwargs: object) -> VerificationFinding:
    defaults = dict(
        verifier_kind="flake8",
        severity="error",
        file_path="src/foo.py",
        line=10,
        message="undefined name",
        rule="F821",
    )
    defaults.update(kwargs)
    return VerificationFinding(**defaults)  # type: ignore[arg-type]


def test_finding_is_frozen() -> None:
    f = _make_finding()
    with pytest.raises((AttributeError, TypeError)):
        f.message = "changed"  # type: ignore[misc]


def test_finding_fields_round_trip() -> None:
    f = _make_finding(line=42, rule="E501", severity="warning")
    assert f.verifier_kind == "flake8"
    assert f.severity == "warning"
    assert f.file_path == "src/foo.py"
    assert f.line == 42
    assert f.rule == "E501"


def test_finding_none_line_allowed() -> None:
    f = _make_finding(line=None)
    assert f.line is None


def test_finding_none_rule_allowed() -> None:
    f = _make_finding(rule=None)
    assert f.rule is None


def test_finding_severity_error() -> None:
    f = _make_finding(severity="error")
    assert f.severity == "error"


def test_finding_severity_warning() -> None:
    f = _make_finding(severity="warning")
    assert f.severity == "warning"


def test_code_verifier_protocol_structural() -> None:
    class StubVerifier:
        kind = "stub"

        def verify(self, workspace_root, written_files):
            return ()

    v: CodeVerifier = StubVerifier()
    from pathlib import Path
    result = v.verify(Path("/tmp"), ())
    assert result == ()
