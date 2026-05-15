from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from backend.App.spec.domain.ports import VerificationFinding


@dataclass(frozen=True)
class CandidateOutcome:
    candidate_id: str
    generated_text: str
    error_count: int
    warning_count: int
    findings: tuple[VerificationFinding, ...]


class NoCandidatePassedError(ValueError):
    def __init__(self, candidates: tuple[CandidateOutcome, ...]) -> None:
        summaries = "; ".join(
            f"{c.candidate_id}: {c.error_count} error(s), {c.warning_count} warning(s)"
            for c in candidates
        )
        super().__init__(
            f"All {len(candidates)} candidate(s) failed verification — {summaries}"
        )
        self.candidates = candidates


def _lowest_error_select(candidates: tuple[CandidateOutcome, ...]) -> CandidateOutcome:
    passing = tuple(c for c in candidates if c.error_count == 0)
    pool = passing if passing else None
    if pool is None:
        raise NoCandidatePassedError(candidates)
    return min(pool, key=lambda c: (c.error_count, c.warning_count))


def _public_names(source: str) -> frozenset[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return frozenset(names)


def majority_vote_select(candidates: tuple[CandidateOutcome, ...]) -> CandidateOutcome:
    if not candidates:
        raise NoCandidatePassedError(candidates)
    name_sets = [_public_names(c.generated_text) for c in candidates]
    vote_counts: dict[int, int] = {}
    for i, names_i in enumerate(name_sets):
        votes = sum(1 for j, names_j in enumerate(name_sets) if i != j and names_i == names_j)
        vote_counts[i] = votes
    max_votes = max(vote_counts.values())
    top_indices = [i for i, v in vote_counts.items() if v == max_votes]
    if len(top_indices) == 1:
        return candidates[top_indices[0]]
    zero_error = [i for i in top_indices if candidates[i].error_count == 0]
    if len(zero_error) == 1:
        return candidates[zero_error[0]]
    if len(zero_error) > 1:
        return min((candidates[i] for i in zero_error), key=lambda c: c.warning_count)
    raise NoCandidatePassedError(candidates)


def select_best_candidate(
    candidates: tuple[CandidateOutcome, ...],
    *,
    strategy: Literal["lowest_error", "majority_vote"] = "lowest_error",
) -> CandidateOutcome:
    if not candidates:
        raise NoCandidatePassedError(candidates)
    if strategy == "majority_vote":
        return majority_vote_select(candidates)
    return _lowest_error_select(candidates)


__all__ = [
    "CandidateOutcome",
    "NoCandidatePassedError",
    "majority_vote_select",
    "select_best_candidate",
]
