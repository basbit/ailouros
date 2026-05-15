from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal

from backend.App.spec.domain.ports import VerificationFinding

_DEFAULT_TIMEOUT = 60
_PATTERN = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):\d+: (?P<rule>[A-Z]\d+) (?P<msg>.+)$")


class SubprocessVerifierError(Exception):
    pass


class Flake8Verifier:
    kind = "flake8"

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
                ["python", "-m", "flake8", *abs_files],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise SubprocessVerifierError(
                "flake8 is not available: python -m flake8 could not be executed"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SubprocessVerifierError(
                f"flake8 timed out after {self._timeout}s"
            ) from exc

        if result.returncode not in (0, 1):
            raise SubprocessVerifierError(
                f"flake8 exited with code {result.returncode}: {result.stderr.strip()}"
            )

        findings: list[VerificationFinding] = []
        for raw_line in result.stdout.splitlines():
            m = _PATTERN.match(raw_line.strip())
            if not m:
                continue
            rule = m.group("rule")
            severity: Literal["error", "warning"] = "warning" if rule.startswith("W") else "error"
            findings.append(
                VerificationFinding(
                    verifier_kind=self.kind,
                    severity=severity,
                    file_path=m.group("path"),
                    line=int(m.group("line")),
                    message=m.group("msg"),
                    rule=rule,
                )
            )
        return tuple(findings)


__all__ = ["Flake8Verifier", "SubprocessVerifierError"]
