from __future__ import annotations

import pytest

from backend.App.spec.domain.candidate_selection import (
    CandidateOutcome,
    NoCandidatePassedError,
    majority_vote_select,
    select_best_candidate,
)
from backend.App.spec.domain.ports import VerificationFinding


def _finding(msg: str = "err", severity: str = "error") -> VerificationFinding:
    return VerificationFinding(
        verifier_kind="test",
        severity=severity,  # type: ignore[arg-type]
        file_path="f.py",
        line=1,
        message=msg,
        rule=None,
    )


def _candidate(
    cid: str,
    errors: int = 0,
    warnings: int = 0,
    text: str = "def foo(): pass\n",
) -> CandidateOutcome:
    findings = tuple(
        [_finding("e", "error") for _ in range(errors)]
        + [_finding("w", "warning") for _ in range(warnings)]
    )
    return CandidateOutcome(
        candidate_id=cid,
        generated_text=text,
        error_count=errors,
        warning_count=warnings,
        findings=findings,
    )


def test_lowest_error_picks_zero_error_candidate() -> None:
    candidates = (
        _candidate("a", errors=2),
        _candidate("b", errors=0, warnings=1),
        _candidate("c", errors=1),
    )
    result = select_best_candidate(candidates, strategy="lowest_error")
    assert result.candidate_id == "b"


def test_lowest_error_breaks_tie_by_warnings() -> None:
    candidates = (
        _candidate("a", errors=0, warnings=3),
        _candidate("b", errors=0, warnings=1),
        _candidate("c", errors=0, warnings=2),
    )
    result = select_best_candidate(candidates, strategy="lowest_error")
    assert result.candidate_id == "b"


def test_all_failed_raises_no_candidate_passed() -> None:
    candidates = (
        _candidate("a", errors=2),
        _candidate("b", errors=1),
    )
    with pytest.raises(NoCandidatePassedError) as exc_info:
        select_best_candidate(candidates, strategy="lowest_error")
    assert "a" in str(exc_info.value)
    assert "b" in str(exc_info.value)


def test_no_candidate_passed_error_carries_candidates() -> None:
    candidates = (_candidate("x", errors=3),)
    with pytest.raises(NoCandidatePassedError) as exc_info:
        select_best_candidate(candidates, strategy="lowest_error")
    assert exc_info.value.candidates == candidates


def test_empty_candidates_raises() -> None:
    with pytest.raises(NoCandidatePassedError):
        select_best_candidate((), strategy="lowest_error")


def test_majority_vote_selects_majority() -> None:
    text_a = "def foo(): pass\ndef bar(): pass\n"
    text_b = "def foo(): pass\ndef bar(): pass\n"
    text_c = "def baz(): pass\n"
    candidates = (
        _candidate("a", text=text_a),
        _candidate("b", text=text_b),
        _candidate("c", text=text_c),
    )
    result = majority_vote_select(candidates)
    assert result.candidate_id in ("a", "b")


def test_majority_vote_tie_broken_by_zero_errors() -> None:
    text_unique = "def alpha(): pass\n"
    text_match1 = "def beta(): pass\n"
    text_match2 = "def beta(): pass\n"
    candidates = (
        _candidate("a", text=text_unique, errors=0),
        _candidate("b", text=text_match1, errors=0),
        _candidate("c", text=text_match2, errors=1),
    )
    result = majority_vote_select(candidates)
    assert result.candidate_id == "b"


def test_majority_vote_full_tie_raises() -> None:
    candidates = (
        _candidate("a", text="def a(): pass\n", errors=1),
        _candidate("b", text="def b(): pass\n", errors=1),
    )
    with pytest.raises(NoCandidatePassedError):
        majority_vote_select(candidates)


def test_select_best_defaults_to_lowest_error() -> None:
    candidates = (
        _candidate("a", errors=1),
        _candidate("b", errors=0),
    )
    result = select_best_candidate(candidates)
    assert result.candidate_id == "b"


def test_majority_vote_strategy_dispatched() -> None:
    text = "def foo(): pass\n"
    candidates = (
        _candidate("a", text=text),
        _candidate("b", text=text),
        _candidate("c", text="def bar(): pass\n"),
    )
    result = select_best_candidate(candidates, strategy="majority_vote")
    assert result.candidate_id in ("a", "b")
