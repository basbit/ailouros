from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.App.spec.infrastructure.verifiers.mutation_verifier import (
    MutationVerifier,
    MutationVerifierError,
)


def _run_result(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _results_json(target: str, total: int, killed: int, survived: int) -> str:
    return json.dumps(
        {
            "files": {
                target: {
                    "total": total,
                    "killed": killed,
                    "survived": survived,
                }
            }
        }
    )


def _patch_mutmut_installed():
    return patch(
        "backend.App.spec.infrastructure.verifiers.mutation_verifier.importlib.util.find_spec",
        return_value=object(),
    )


def test_missing_mutmut_raises(tmp_path: Path) -> None:
    with patch(
        "backend.App.spec.infrastructure.verifiers.mutation_verifier.importlib.util.find_spec",
        return_value=None,
    ):
        with pytest.raises(MutationVerifierError, match="mutmut is not installed"):
            MutationVerifier().verify(tmp_path, ("src/foo.py",))


def test_no_targets_raises(tmp_path: Path) -> None:
    with pytest.raises(MutationVerifierError, match="at least one target"):
        MutationVerifier().verify(tmp_path, ())


def test_score_below_threshold_returns_warning(tmp_path: Path) -> None:
    target = "src/foo.py"
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(target, total=10, killed=4, survived=6), returncode=0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        findings = MutationVerifier(threshold=0.6).verify(tmp_path, (target,))
    assert len(findings) == 1
    f = findings[0]
    assert f.verifier_kind == "mutation"
    assert f.severity == "warning"
    assert f.file_path == target
    assert "score 0.40 below threshold 0.60" in f.message


def test_score_above_threshold_returns_no_finding(tmp_path: Path) -> None:
    target = "src/foo.py"
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(target, total=10, killed=8, survived=2), returncode=0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        findings = MutationVerifier(threshold=0.6).verify(tmp_path, (target,))
    assert findings == ()


def test_score_equal_threshold_returns_no_finding(tmp_path: Path) -> None:
    target = "src/foo.py"
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(target, total=10, killed=6, survived=4), returncode=0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        findings = MutationVerifier(threshold=0.6).verify(tmp_path, (target,))
    assert findings == ()


def test_mutmut_run_timeout_raises(tmp_path: Path) -> None:
    with _patch_mutmut_installed(), patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="mutmut", timeout=600),
    ):
        with pytest.raises(MutationVerifierError, match="timed out"):
            MutationVerifier().verify(tmp_path, ("src/foo.py",))


def test_mutmut_results_returncode_nonzero_raises(tmp_path: Path) -> None:
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result("boom", returncode=5, stderr="bad"),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        with pytest.raises(MutationVerifierError, match="results exited with code 5"):
            MutationVerifier().verify(tmp_path, ("src/foo.py",))


def test_invalid_json_payload_raises(tmp_path: Path) -> None:
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result("{not json", returncode=0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        with pytest.raises(MutationVerifierError, match="failed to parse"):
            MutationVerifier().verify(tmp_path, ("src/foo.py",))


def test_missing_target_in_json_raises(tmp_path: Path) -> None:
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json("src/other.py", total=10, killed=5, survived=5), 0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        with pytest.raises(MutationVerifierError, match="no entry for target"):
            MutationVerifier().verify(tmp_path, ("src/foo.py",))


def test_threshold_env_override(tmp_path: Path) -> None:
    target = "src/foo.py"
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(target, total=10, killed=9, survived=1), returncode=0),
    ]
    os.environ["SWARM_MUTATION_SCORE_MIN"] = "0.95"
    try:
        with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
            findings = MutationVerifier().verify(tmp_path, (target,))
    finally:
        del os.environ["SWARM_MUTATION_SCORE_MIN"]
    assert len(findings) == 1
    assert "below threshold 0.95" in findings[0].message


def test_invalid_threshold_env_raises(tmp_path: Path) -> None:
    os.environ["SWARM_MUTATION_SCORE_MIN"] = "not-a-float"
    try:
        with _patch_mutmut_installed():
            with pytest.raises(MutationVerifierError, match="must be a float"):
                MutationVerifier().verify(tmp_path, ("src/foo.py",))
    finally:
        del os.environ["SWARM_MUTATION_SCORE_MIN"]


def test_invalid_per_mutant_env_raises(tmp_path: Path) -> None:
    os.environ["SWARM_MUTATION_PER_MUTANT_SEC"] = "0"
    try:
        with _patch_mutmut_installed():
            with pytest.raises(MutationVerifierError, match=r"must be > 0"):
                MutationVerifier().verify(tmp_path, ("src/foo.py",))
    finally:
        del os.environ["SWARM_MUTATION_PER_MUTANT_SEC"]


def test_zero_total_mutants_propagates_domain_error(tmp_path: Path) -> None:
    target = "src/foo.py"
    side_effect = [
        _run_result("ok", returncode=0),
        _run_result(_results_json(target, total=0, killed=0, survived=0), returncode=0),
    ]
    with _patch_mutmut_installed(), patch("subprocess.run", side_effect=side_effect):
        with pytest.raises(MutationVerifierError, match="mutants_total == 0"):
            MutationVerifier().verify(tmp_path, (target,))
