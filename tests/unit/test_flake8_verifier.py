from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.spec.infrastructure.verifiers.flake8_verifier import (
    Flake8Verifier,
    SubprocessVerifierError,
)


def _run_result(stdout: str, returncode: int = 1) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


_SAMPLE_OUTPUT = (
    "src/foo.py:5:1: E302 expected 2 blank lines, found 1\n"
    "src/foo.py:10:80: W503 line break before binary operator\n"
    "src/bar.py:3:1: F821 undefined name 'x'\n"
)


def test_parses_error_findings(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = Flake8Verifier().verify(tmp_path, ("src/foo.py",))
    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 2


def test_parses_warning_findings(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = Flake8Verifier().verify(tmp_path, ("src/foo.py",))
    warnings = [f for f in findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert warnings[0].rule == "W503"


def test_finding_fields(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = Flake8Verifier().verify(tmp_path, ("src/foo.py",))
    e302 = next(f for f in findings if f.rule == "E302")
    assert e302.line == 5
    assert e302.verifier_kind == "flake8"
    assert "blank lines" in e302.message


def test_clean_output_returns_empty(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result("", returncode=0)):
        findings = Flake8Verifier().verify(tmp_path, ("src/foo.py",))
    assert findings == ()


def test_subprocess_error_on_returncode_2(tmp_path: Path) -> None:
    result = _run_result("fatal error", returncode=2)
    result.stderr = "fatal error"
    with patch("subprocess.run", return_value=result):
        with pytest.raises(SubprocessVerifierError, match="exited with code 2"):
            Flake8Verifier().verify(tmp_path, ("src/foo.py",))


def test_file_not_found_raises_subprocess_error(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SubprocessVerifierError, match="not available"):
            Flake8Verifier().verify(tmp_path, ("src/foo.py",))


def test_timeout_raises_subprocess_error(tmp_path: Path) -> None:
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="flake8", timeout=60)):
        with pytest.raises(SubprocessVerifierError, match="timed out"):
            Flake8Verifier().verify(tmp_path, ("src/foo.py",))
