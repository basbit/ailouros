from __future__ import annotations

from dataclasses import dataclass

from backend.App.spec.domain.ports import VerificationFinding


@dataclass(frozen=True)
class RetryDiagnostic:
    attempt: int
    previous_code: str
    findings: tuple[VerificationFinding, ...]


def format_diagnostic(diagnostic: RetryDiagnostic) -> str:
    error_lines: list[str] = []
    for f in diagnostic.findings:
        location = f"{f.file_path}:{f.line}" if f.line is not None else f.file_path
        rule_tag = f" [{f.rule}]" if f.rule else ""
        error_lines.append(f"  [{f.severity.upper()}] {location} — {f.message}{rule_tag}")

    errors_block = "\n".join(error_lines) if error_lines else "  (no structured findings)"

    return (
        f"## Attempt {diagnostic.attempt} failed — static analysis findings\n\n"
        f"The previously generated code contained the following issues that must be fixed:\n\n"
        f"{errors_block}\n\n"
        f"### Previously generated code (attempt {diagnostic.attempt})\n\n"
        f"```python\n{diagnostic.previous_code}\n```\n\n"
        f"Rewrite the code to fix ALL of the above issues. "
        f"Do not repeat the same mistakes.\n"
    )


__all__ = ["RetryDiagnostic", "format_diagnostic"]
