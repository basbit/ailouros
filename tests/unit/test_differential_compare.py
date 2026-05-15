from __future__ import annotations

import pytest

from backend.App.spec.domain.differential_compare import (
    DifferentialReport,
    compare_outputs,
    compare_verifier_findings,
)
from backend.App.spec.domain.ports import VerificationFinding


_PY_A = """\
def login(user: str, pw: str) -> bool:
    return True

class AuthService:
    pass

MAX_ATTEMPTS = 3
"""

_PY_B = """\
def login(user: str, pw: str) -> bool:
    return True

class AuthService:
    pass

MAX_ATTEMPTS = 3
"""

_PY_EXTRA_FUNC = """\
def login(user: str, pw: str) -> bool:
    return True

def logout(user: str) -> None:
    pass

class AuthService:
    pass

MAX_ATTEMPTS = 3
"""

_PY_RENAMED = """\
def authenticate(user: str, pw: str) -> bool:
    return True

class AuthService:
    pass

MAX_ATTEMPTS = 3
"""

_TS_A = """\
export function login(user: string, pw: string): boolean { return true; }
export class AuthService {}
export const MAX_ATTEMPTS = 3;
"""

_TS_B = """\
export function login(user: string, pw: string): boolean { return true; }
export class AuthService {}
export const MAX_ATTEMPTS = 3;
"""

_TS_EXTRA = """\
export function login(user: string, pw: string): boolean { return true; }
export function logout(user: string): void {}
export class AuthService {}
export const MAX_ATTEMPTS = 3;
"""


def test_identical_python_no_surface_diff() -> None:
    report = compare_outputs(_PY_A, _PY_B, language="python")
    surface_findings = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert surface_findings == []


def test_identical_python_agreement_ratio_one() -> None:
    report = compare_outputs(_PY_A, _PY_B, language="python")
    assert report.agreement_ratio == pytest.approx(1.0)


def test_added_function_detected_as_surface_diff() -> None:
    report = compare_outputs(_PY_A, _PY_EXTRA_FUNC, language="python")
    surface = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert len(surface) == 1
    assert surface[0].severity == "error"
    assert "logout" in surface[0].details["only_in_b"]


def test_renamed_function_detected_as_surface_diff() -> None:
    report = compare_outputs(_PY_A, _PY_RENAMED, language="python")
    surface = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert len(surface) == 1
    assert "login" in surface[0].details["only_in_a"]
    assert "authenticate" in surface[0].details["only_in_b"]


def test_typescript_identical_no_surface_diff() -> None:
    report = compare_outputs(_TS_A, _TS_B, language="typescript")
    surface = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert surface == []


def test_typescript_extra_export_detected() -> None:
    report = compare_outputs(_TS_A, _TS_EXTRA, language="typescript")
    surface = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert len(surface) == 1
    assert "logout" in surface[0].details["only_in_b"]


def test_line_diff_finding_above_threshold() -> None:
    many_lines_a = "\n".join(f"x_{i} = {i}" for i in range(50))
    many_lines_b = "\n".join(f"y_{i} = {i * 2}" for i in range(50))
    report = compare_outputs(many_lines_a, many_lines_b, language="python")
    line_findings = [f for f in report.findings if f.kind == "line_diff"]
    assert len(line_findings) == 1
    assert line_findings[0].severity == "warning"


def test_line_diff_finding_absent_below_threshold() -> None:
    base = "\n".join(f"x_{i} = {i}" for i in range(100))
    almost_same = base + "\nx_extra = 999"
    report = compare_outputs(base, almost_same, language="python")
    line_findings = [f for f in report.findings if f.kind == "line_diff"]
    assert line_findings == []


def test_agreement_ratio_math_high_diff() -> None:
    many_lines_a = "\n".join(f"a_{i} = {i}" for i in range(50))
    many_lines_b = "\n".join(f"b_{i} = {i}" for i in range(50))
    report = compare_outputs(many_lines_a, many_lines_b, language="python")
    assert report.agreement_ratio < 0.7


def test_agreement_ratio_math_low_diff() -> None:
    base = "\n".join(f"x_{i} = {i}" for i in range(100))
    tiny_change = base.replace("x_0 = 0", "x_0 = 999")
    report = compare_outputs(base, tiny_change, language="python")
    assert report.agreement_ratio > 0.97


def test_auto_language_detection_picks_python() -> None:
    report = compare_outputs(_PY_A, _PY_B, language="auto")
    assert isinstance(report, DifferentialReport)


def test_auto_language_detection_picks_typescript() -> None:
    report = compare_outputs(_TS_A, _TS_B, language="auto")
    assert isinstance(report, DifferentialReport)


def test_compare_verifier_findings_identical_returns_empty() -> None:
    finding = VerificationFinding(
        verifier_kind="flake8",
        severity="error",
        file_path="src/foo.py",
        line=10,
        message="E302 expected 2 blank lines",
        rule="E302",
    )
    result = compare_verifier_findings((finding,), (finding,))
    assert result == ()


def test_compare_verifier_findings_disagreement_detected() -> None:
    a_finding = VerificationFinding(
        verifier_kind="mypy",
        severity="error",
        file_path="src/foo.py",
        line=5,
        message="Incompatible return value",
        rule=None,
    )
    b_finding = VerificationFinding(
        verifier_kind="mypy",
        severity="error",
        file_path="src/foo.py",
        line=5,
        message="Missing return statement",
        rule=None,
    )
    result = compare_verifier_findings((a_finding,), (b_finding,))
    assert len(result) == 1
    assert result[0].kind == "verifier_disagreement"
    assert result[0].severity == "warning"


def test_compare_verifier_findings_counts_in_details() -> None:
    a_findings = tuple(
        VerificationFinding("flake8", "error", "f.py", i, f"msg_{i}", None)
        for i in range(3)
    )
    b_findings = tuple(
        VerificationFinding("flake8", "error", "f.py", i + 10, f"other_{i}", None)
        for i in range(2)
    )
    result = compare_verifier_findings(a_findings, b_findings)
    assert len(result) == 1
    assert result[0].details["only_in_a_count"] == "3"
    assert result[0].details["only_in_b_count"] == "2"


def test_report_model_names_default_empty() -> None:
    report = compare_outputs(_PY_A, _PY_B, language="python")
    assert report.model_a == ""
    assert report.model_b == ""


def test_syntax_error_python_returns_empty_surface() -> None:
    broken = "def (((invalid python:"
    report = compare_outputs(broken, _PY_A, language="python")
    surface = [f for f in report.findings if f.kind == "public_surface_diff"]
    assert len(surface) == 1
    assert surface[0].details["only_in_a"] == "(none)"
