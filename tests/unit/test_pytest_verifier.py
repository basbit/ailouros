from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.spec.infrastructure.verifiers.flake8_verifier import SubprocessVerifierError
from backend.App.spec.infrastructure.verifiers.pytest_verifier import PytestVerifier


def _run_result(stdout: str, returncode: int = 1) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


_FAILED_OUTPUT = (
    "FAILED tests/unit/test_auth.py::test_login - AssertionError: assert False\n"
    "FAILED tests/unit/test_auth.py::test_logout\n"
    "2 failed, 5 passed in 0.42s\n"
)


def test_parses_failed_tests(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_FAILED_OUTPUT)):
        findings = PytestVerifier().verify(tmp_path, ())
    assert len(findings) == 2
    assert all(f.severity == "error" for f in findings)
    assert all(f.verifier_kind == "pytest" for f in findings)


def test_finding_includes_test_name(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_FAILED_OUTPUT)):
        findings = PytestVerifier().verify(tmp_path, ())
    assert any("test_login" in f.message for f in findings)


def test_clean_run_returns_empty(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result("1 passed in 0.01s", returncode=0)):
        findings = PytestVerifier().verify(tmp_path, ())
    assert findings == ()


def test_no_parsed_lines_falls_back_to_single_finding(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result("something went wrong\n2 failed")):
        findings = PytestVerifier().verify(tmp_path, ())
    assert len(findings) >= 1
    assert findings[0].severity == "error"


def test_returncode_2_raises(tmp_path: Path) -> None:
    result = _run_result("internal error", returncode=2)
    result.stderr = "internal error"
    with patch("subprocess.run", return_value=result):
        with pytest.raises(SubprocessVerifierError, match="exited with code 2"):
            PytestVerifier().verify(tmp_path, ())


def test_file_not_found_raises(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SubprocessVerifierError, match="not available"):
            PytestVerifier().verify(tmp_path, ())


def test_timeout_raises(tmp_path: Path) -> None:
    import subprocess
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=60),
    ):
        with pytest.raises(SubprocessVerifierError, match="timed out"):
            PytestVerifier().verify(tmp_path, ())
