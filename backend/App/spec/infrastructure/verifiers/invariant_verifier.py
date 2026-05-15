from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from backend.App.spec.application.invariant_test_generator import (
    InvariantGeneratorError,
    generate_property_test_module,
)
from backend.App.spec.domain.dsl_block import extract_dsl_blocks
from backend.App.spec.domain.dsl_invariants import InvariantsParser
from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.infrastructure.verifiers.flake8_verifier import SubprocessVerifierError

import os

_DEFAULT_TIMEOUT = 60
_PARSER = InvariantsParser()


def _build_env(extra_pythonpath: tuple[str, ...]) -> dict[str, str]:
    env = dict(os.environ)
    if extra_pythonpath:
        existing = env.get("PYTHONPATH", "")
        parts = list(extra_pythonpath) + ([existing] if existing else [])
        env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


class InvariantVerifier:
    kind = "invariants"

    def __init__(
        self,
        spec_id: str,
        spec_body: str,
        fixture_module: str,
        timeout: int = _DEFAULT_TIMEOUT,
        extra_pythonpath: tuple[str, ...] = (),
    ) -> None:
        self._spec_id = spec_id
        self._spec_body = spec_body
        self._fixture_module = fixture_module
        self._timeout = timeout
        self._extra_pythonpath = extra_pythonpath

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        blocks = [b for b in extract_dsl_blocks(self._spec_body) if b.kind == "invariants"]
        if not blocks:
            return ()

        all_invariants: list[dict[str, str]] = []
        for block in blocks:
            result = _PARSER.parse(block)
            parse_errors = [f for f in result.findings if f.severity == "error"]
            if parse_errors:
                return tuple(
                    VerificationFinding(
                        verifier_kind=self.kind,
                        severity="error",
                        file_path=f"<spec:{self._spec_id}>",
                        line=None,
                        message=f.message,
                        rule="invariants-parse",
                    )
                    for f in parse_errors
                )
            all_invariants.extend(result.payload.get("invariants", []))

        try:
            source = generate_property_test_module(
                self._spec_id,
                all_invariants,
                fixture_module=self._fixture_module,
            )
        except InvariantGeneratorError as exc:
            raise SubprocessVerifierError(
                f"spec {self._spec_id!r}: invariant test generation failed: {exc}"
            ) from exc

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            test_file = tmp / f"test_invariants_{self._spec_id}.py"
            test_file.write_text(source, encoding="utf-8")

            env = _build_env(self._extra_pythonpath)
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "--tb=short", "-q", str(test_file)],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    cwd=str(workspace_root),
                    env=env,
                )
            except FileNotFoundError as exc:
                raise SubprocessVerifierError(
                    "pytest is not available: python -m pytest could not be executed"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise SubprocessVerifierError(
                    f"invariant hypothesis tests timed out after {self._timeout}s"
                ) from exc

            if proc.returncode == 0:
                return ()

            combined = (proc.stdout + proc.stderr).strip()
            return (
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity="error",
                    file_path=f"<spec:{self._spec_id}:invariants>",
                    line=None,
                    message=combined[:1000] or "hypothesis invariant tests failed",
                    rule="invariants-hypothesis",
                ),
            )


__all__ = ["InvariantVerifier"]
