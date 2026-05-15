from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.spec.infrastructure.verifiers.flake8_verifier import SubprocessVerifierError
from backend.App.spec.infrastructure.verifiers.mypy_verifier import MypyVerifier


def _run_result(stdout: str, returncode: int = 1) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


_SAMPLE_OUTPUT = (
    "src/foo.py:5:1: error: Argument 1 to \"foo\" has incompatible type [arg-type]\n"
    "src/foo.py:10:1: warning: Skipping analyzing 'bar' [import]\n"
    "src/foo.py:12:1: note: See https://mypy.readthedocs.io\n"
)


def test_parses_error(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = MypyVerifier().verify(tmp_path, ("src/foo.py",))
    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 1
    assert errors[0].rule == "arg-type"


def test_parses_warning(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = MypyVerifier().verify(tmp_path, ("src/foo.py",))
    warnings = [f for f in findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert warnings[0].rule == "import"


def test_notes_are_excluded(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = MypyVerifier().verify(tmp_path, ("src/foo.py",))
    assert all(f.severity in ("error", "warning") for f in findings)
    assert len(findings) == 2


def test_clean_output_returns_empty(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result("", returncode=0)):
        findings = MypyVerifier().verify(tmp_path, ("src/foo.py",))
    assert findings == ()


def test_returncode_2_raises(tmp_path: Path) -> None:
    result = _run_result("crash", returncode=2)
    result.stderr = "crash"
    with patch("subprocess.run", return_value=result):
        with pytest.raises(SubprocessVerifierError, match="exited with code 2"):
            MypyVerifier().verify(tmp_path, ("src/foo.py",))


def test_file_not_found_raises(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SubprocessVerifierError, match="not available"):
            MypyVerifier().verify(tmp_path, ("src/foo.py",))


def test_finding_line_number(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_run_result(_SAMPLE_OUTPUT)):
        findings = MypyVerifier().verify(tmp_path, ("src/foo.py",))
    errors = [f for f in findings if f.severity == "error"]
    assert errors[0].line == 5
