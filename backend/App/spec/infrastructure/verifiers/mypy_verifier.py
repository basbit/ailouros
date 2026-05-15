from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal

from backend.App.spec.domain.ports import VerificationFinding
from backend.App.spec.infrastructure.verifiers.flake8_verifier import SubprocessVerifierError

_DEFAULT_TIMEOUT = 60
_PATTERN = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):\d+: (?P<severity>error|warning|note): (?P<msg>.+?)(?:\s+\[(?P<rule>[^\]]+)\])?$"
)


class MypyVerifier:
    kind = "mypy"

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    def verify(
        self,
        workspace_root: Path,
        written_files: tuple[str, ...],
    ) -> tuple[VerificationFinding, ...]:
        abs_files = [str(workspace_root / f) for f in written_files]
        try:
            result = subprocess.run(
                ["python", "-m", "mypy", "--ignore-missing-imports", *abs_files],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise SubprocessVerifierError(
                "mypy is not available: python -m mypy could not be executed"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SubprocessVerifierError(
                f"mypy timed out after {self._timeout}s"
            ) from exc

        if result.returncode not in (0, 1):
            raise SubprocessVerifierError(
                f"mypy exited with code {result.returncode}: {result.stderr.strip()}"
            )

        findings: list[VerificationFinding] = []
        for raw_line in result.stdout.splitlines():
            m = _PATTERN.match(raw_line.strip())
            if not m:
                continue
            raw_sev = m.group("severity")
            if raw_sev == "note":
                continue
            severity: Literal["error", "warning"] = "error" if raw_sev == "error" else "warning"
            findings.append(
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity=severity,
                    file_path=m.group("path"),
                    line=int(m.group("line")),
                    message=m.group("msg"),
                    rule=m.group("rule"),
                )
            )
        return tuple(findings)


__all__ = ["MypyVerifier"]
