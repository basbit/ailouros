from __future__ import annotations

import re
import subprocess
from pathlib import Path

from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.infrastructure.verifiers.flake8_verifier import SubprocessVerifierError

_DEFAULT_TIMEOUT = 60
_FAILED_LINE = re.compile(r"^FAILED (?P<path>[^:]+)::(?P<test>.+?)(?:\s+-\s+(?P<msg>.+))?$")
_SHORT_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (?P<path>[^:]+)(?:::(?P<test>[^\s]+))?(?: - (?P<msg>.+))?$")


class PytestVerifier:
    kind = "pytest"

    def __init__(
        self,
        test_targets: tuple[str, ...] = (),
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._test_targets = test_targets
        self._timeout = timeout

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        targets = list(self._test_targets) if self._test_targets else []
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "--tb=no", "-q", *targets],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=str(workspace_root),
            )
        except FileNotFoundError as exc:
            raise SubprocessVerifierError(
                "pytest is not available: python -m pytest could not be executed"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SubprocessVerifierError(
                f"pytest timed out after {self._timeout}s"
            ) from exc

        if result.returncode not in (0, 1):
            raise SubprocessVerifierError(
                f"pytest exited with code {result.returncode}: {result.stderr.strip()}"
            )

        if result.returncode == 0:
            return ()

        findings: list[VerificationFinding] = []
        combined = result.stdout + result.stderr
        for raw_line in combined.splitlines():
            m = _FAILED_LINE.match(raw_line.strip()) or _SHORT_SUMMARY.match(raw_line.strip())
            if not m:
                continue
            test_path = m.group("path")
            test_id = m.group("test") if "test" in m.groupdict() else None
            msg = m.group("msg") or "test failed"
            findings.append(
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity="error",
                    file_path=test_path,
                    line=None,
                    message=f"{test_id}: {msg}" if test_id else msg,
                    rule=None,
                )
            )
        if not findings:
            findings.append(
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity="error",
                    file_path="<pytest>",
                    line=None,
                    message=combined.strip()[:500] or "pytest reported failures",
                    rule=None,
                )
            )
        return tuple(findings)


__all__ = ["PytestVerifier"]
