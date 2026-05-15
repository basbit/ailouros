from __future__ import annotations

from dataclasses import dataclass


class MutationDomainError(Exception):
    pass


@dataclass(frozen=True)
class MutationStats:
    target_path: str
    mutants_total: int
    mutants_killed: int
    mutants_survived: int

    def __post_init__(self) -> None:
        if self.mutants_total < 0:
            raise MutationDomainError(
                f"mutants_total must be >= 0, got {self.mutants_total}"
            )
        if self.mutants_killed < 0:
            raise MutationDomainError(
                f"mutants_killed must be >= 0, got {self.mutants_killed}"
            )
        if self.mutants_survived < 0:
            raise MutationDomainError(
                f"mutants_survived must be >= 0, got {self.mutants_survived}"
            )
        if self.mutants_killed + self.mutants_survived > self.mutants_total:
            raise MutationDomainError(
                f"killed + survived ({self.mutants_killed} + {self.mutants_survived}) "
                f"exceeds total {self.mutants_total} for {self.target_path!r}"
            )


def mutation_score(stats: MutationStats) -> float:
    if stats.mutants_total == 0:
        raise MutationDomainError(
            f"cannot compute mutation score for {stats.target_path!r}: "
            f"mutants_total == 0 (no mutants were generated)"
        )
    return stats.mutants_killed / stats.mutants_total


def below_threshold(stats: MutationStats, threshold: float) -> bool:
    if threshold < 0.0 or threshold > 1.0:
        raise MutationDomainError(
            f"threshold must be in [0.0, 1.0], got {threshold}"
        )
    return mutation_score(stats) < threshold


__all__ = [
    "MutationDomainError",
    "MutationStats",
    "below_threshold",
    "mutation_score",
]
