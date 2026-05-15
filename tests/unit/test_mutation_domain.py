from __future__ import annotations

import pytest

from backend.App.spec.domain.mutation_finding import (
    MutationDomainError,
    MutationStats,
    below_threshold,
    mutation_score,
)


def test_mutation_score_basic() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=10,
        mutants_killed=8,
        mutants_survived=2,
    )
    assert mutation_score(stats) == pytest.approx(0.8)


def test_mutation_score_all_killed_is_one() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=5,
        mutants_killed=5,
        mutants_survived=0,
    )
    assert mutation_score(stats) == pytest.approx(1.0)


def test_mutation_score_none_killed_is_zero() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=5,
        mutants_killed=0,
        mutants_survived=5,
    )
    assert mutation_score(stats) == pytest.approx(0.0)


def test_mutation_score_zero_total_raises() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=0,
        mutants_killed=0,
        mutants_survived=0,
    )
    with pytest.raises(MutationDomainError, match="mutants_total == 0"):
        mutation_score(stats)


def test_mutation_stats_negative_total_raises() -> None:
    with pytest.raises(MutationDomainError, match="mutants_total"):
        MutationStats(
            target_path="src/foo.py",
            mutants_total=-1,
            mutants_killed=0,
            mutants_survived=0,
        )


def test_mutation_stats_killed_plus_survived_exceeds_total_raises() -> None:
    with pytest.raises(MutationDomainError, match="exceeds total"):
        MutationStats(
            target_path="src/foo.py",
            mutants_total=10,
            mutants_killed=8,
            mutants_survived=5,
        )


def test_below_threshold_true_when_score_under() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=10,
        mutants_killed=4,
        mutants_survived=6,
    )
    assert below_threshold(stats, 0.6) is True


def test_below_threshold_false_when_score_equal_or_above() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=10,
        mutants_killed=6,
        mutants_survived=4,
    )
    assert below_threshold(stats, 0.6) is False


def test_below_threshold_rejects_out_of_range() -> None:
    stats = MutationStats(
        target_path="src/foo.py",
        mutants_total=10,
        mutants_killed=5,
        mutants_survived=5,
    )
    with pytest.raises(MutationDomainError, match=r"threshold must be in"):
        below_threshold(stats, 1.5)
    with pytest.raises(MutationDomainError, match=r"threshold must be in"):
        below_threshold(stats, -0.1)
