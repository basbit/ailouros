from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Sequence

from backend.App.spec.domain.mutation_finding import (
    MutationDomainError,
    MutationStats,
    mutation_score,
)
from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.infrastructure.verifiers.flake8_verifier import (
    SubprocessVerifierError,
)

_ENV_THRESHOLD = "SWARM_MUTATION_SCORE_MIN"
_ENV_PER_MUTANT = "SWARM_MUTATION_PER_MUTANT_SEC"
_ENV_GLOBAL_TIMEOUT = "SWARM_MUTATION_GLOBAL_TIMEOUT_SEC"

_DEFAULT_THRESHOLD = 0.6
_DEFAULT_PER_MUTANT_SEC = 5
_DEFAULT_GLOBAL_TIMEOUT_SEC = 600


class MutationVerifierError(SubprocessVerifierError):
    pass


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise MutationVerifierError(
            f"{name} must be a float, got {raw!r}"
        ) from exc
    if value < 0.0 or value > 1.0:
        raise MutationVerifierError(
            f"{name} must be in [0.0, 1.0], got {value}"
        )
    return value


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise MutationVerifierError(
            f"{name} must be an int, got {raw!r}"
        ) from exc
    if value <= 0:
        raise MutationVerifierError(
            f"{name} must be > 0, got {value}"
        )
    return value


def _require_mutmut() -> None:
    if importlib.util.find_spec("mutmut") is None:
        raise MutationVerifierError(
            "mutmut is not installed. Install via "
            "`pip install -r requirements-mutation.txt` "
            "(or `pip install 'mutmut>=2.5,<3'`)."
        )


def _parse_results_json(payload: str, targets: Sequence[str]) -> tuple[MutationStats, ...]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise MutationVerifierError(
            f"failed to parse mutmut JSON output: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise MutationVerifierError(
            f"unexpected mutmut JSON shape: top-level is {type(data).__name__}, expected object"
        )

    files = data.get("files")
    if not isinstance(files, dict):
        raise MutationVerifierError(
            "mutmut JSON missing 'files' object"
        )

    stats_list: list[MutationStats] = []
    for target in targets:
        entry = files.get(target)
        if entry is None:
            raise MutationVerifierError(
                f"mutmut JSON has no entry for target {target!r}; "
                f"available: {sorted(files.keys())}"
            )
        if not isinstance(entry, dict):
            raise MutationVerifierError(
                f"mutmut JSON entry for {target!r} is not an object"
            )
        total = entry.get("total")
        killed = entry.get("killed")
        survived = entry.get("survived")
        if not isinstance(total, int) or not isinstance(killed, int) or not isinstance(survived, int):
            raise MutationVerifierError(
                f"mutmut JSON entry for {target!r} missing int totals: {entry!r}"
            )
        stats_list.append(
            MutationStats(
                target_path=target,
                mutants_total=total,
                mutants_killed=killed,
                mutants_survived=survived,
            )
        )
    return tuple(stats_list)


class MutationVerifier:
    kind = "mutation"

    def __init__(
        self,
        threshold: float | None = None,
        per_mutant_timeout: int | None = None,
        global_timeout: int | None = None,
    ) -> None:
        self._threshold_override = threshold
        self._per_mutant_override = per_mutant_timeout
        self._global_timeout_override = global_timeout

    def _resolve_threshold(self) -> float:
        if self._threshold_override is not None:
            return self._threshold_override
        return _read_float_env(_ENV_THRESHOLD, _DEFAULT_THRESHOLD)

    def _resolve_per_mutant(self) -> int:
        if self._per_mutant_override is not None:
            return self._per_mutant_override
        return _read_int_env(_ENV_PER_MUTANT, _DEFAULT_PER_MUTANT_SEC)

    def _resolve_global_timeout(self) -> int:
        if self._global_timeout_override is not None:
            return self._global_timeout_override
        return _read_int_env(_ENV_GLOBAL_TIMEOUT, _DEFAULT_GLOBAL_TIMEOUT_SEC)

    def run(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[MutationStats, ...]:
        if not written_files:
            raise MutationVerifierError(
                "MutationVerifier requires at least one target file"
            )
        _require_mutmut()
        per_mutant = self._resolve_per_mutant()
        global_timeout = self._resolve_global_timeout()
        targets = list(written_files)
        try:
            run_result = subprocess.run(
                [
                    "python",
                    "-m",
                    "mutmut",
                    "run",
                    "--paths-to-mutate",
                    ",".join(targets),
                    "--tests-dir",
                    "tests",
                    "--runner",
                    f"python -m pytest -x --timeout={per_mutant}",
                ],
                capture_output=True,
                text=True,
                timeout=global_timeout,
                cwd=str(workspace_root),
            )
        except FileNotFoundError as exc:
            raise MutationVerifierError(
                "mutmut is not available: python -m mutmut could not be executed"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise MutationVerifierError(
                f"mutmut run timed out after {global_timeout}s "
                f"(override via {_ENV_GLOBAL_TIMEOUT})"
            ) from exc

        if run_result.returncode not in (0, 1, 2):
            raise MutationVerifierError(
                f"mutmut run exited with code {run_result.returncode}: "
                f"{run_result.stderr.strip()}"
            )

        try:
            results = subprocess.run(
                ["python", "-m", "mutmut", "results", "--json"],
                capture_output=True,
                text=True,
                timeout=global_timeout,
                cwd=str(workspace_root),
            )
        except subprocess.TimeoutExpired as exc:
            raise MutationVerifierError(
                f"mutmut results timed out after {global_timeout}s"
            ) from exc

        if results.returncode != 0:
            raise MutationVerifierError(
                f"mutmut results exited with code {results.returncode}: "
                f"{results.stderr.strip()}"
            )

        return _parse_results_json(results.stdout, targets)

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        threshold = self._resolve_threshold()
        stats_tuple = self.run(workspace_root, written_files)
        findings: list[VerificationFinding] = []
        for stats in stats_tuple:
            try:
                score = mutation_score(stats)
            except MutationDomainError as exc:
                raise MutationVerifierError(str(exc)) from exc
            if score < threshold:
                findings.append(
                    VerificationFinding(
                        verifier_kind=self.kind,
                        severity="warning",
                        file_path=stats.target_path,
                        line=None,
                        message=(
                            f"score {score:.2f} below threshold {threshold:.2f} "
                            f"(killed {stats.mutants_killed}/{stats.mutants_total})"
                        ),
                        rule=None,
                    )
                )
        return tuple(findings)


__all__ = [
    "MutationVerifier",
    "MutationVerifierError",
]
