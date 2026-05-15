from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass
from typing import Literal

from backend.App.spec.domain.ports import VerificationFinding

_TS_EXPORT_RE = re.compile(
    r"^export\s+(?:(?:async\s+)?function|class|const|let|var|type|interface|enum)\s+(\w+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DifferentialFinding:
    kind: Literal["public_surface_diff", "line_diff", "verifier_disagreement"]
    severity: Literal["error", "warning", "info"]
    message: str
    details: dict[str, str]


@dataclass(frozen=True)
class DifferentialReport:
    model_a: str
    model_b: str
    findings: tuple[DifferentialFinding, ...]
    agreement_ratio: float


def _detect_language(output_a: str, output_b: str) -> Literal["python", "typescript"]:
    combined = output_a + output_b
    py_score = combined.count("def ") + combined.count("import ") + combined.count("class ")
    ts_score = combined.count("export ") + combined.count("interface ") + combined.count(": void")
    return "typescript" if ts_score > py_score else "python"


def _python_exported_names(source: str) -> frozenset[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                names.add(node.target.id)
    return frozenset(names)


def _typescript_exported_names(source: str) -> frozenset[str]:
    return frozenset(_TS_EXPORT_RE.findall(source))


def _line_diff_ratio(output_a: str, output_b: str) -> float:
    lines_a = output_a.splitlines()
    lines_b = output_b.splitlines()
    if not lines_a and not lines_b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    return 1.0 - matcher.ratio()


def compare_outputs(
    output_a: str,
    output_b: str,
    *,
    language: Literal["python", "typescript", "auto"] = "auto",
) -> DifferentialReport:
    effective_lang: Literal["python", "typescript"] = (
        _detect_language(output_a, output_b) if language == "auto" else language
    )

    findings: list[DifferentialFinding] = []

    if effective_lang == "python":
        names_a = _python_exported_names(output_a)
        names_b = _python_exported_names(output_b)
    else:
        names_a = _typescript_exported_names(output_a)
        names_b = _typescript_exported_names(output_b)

    only_in_a = names_a - names_b
    only_in_b = names_b - names_a

    if only_in_a or only_in_b:
        findings.append(
            DifferentialFinding(
                kind="public_surface_diff",
                severity="error",
                message=(
                    f"Public surface mismatch: {len(only_in_a)} name(s) only in model_a, "
                    f"{len(only_in_b)} name(s) only in model_b"
                ),
                details={
                    "only_in_a": ", ".join(sorted(only_in_a)) or "(none)",
                    "only_in_b": ", ".join(sorted(only_in_b)) or "(none)",
                    "language": effective_lang,
                },
            )
        )

    line_diff_ratio = _line_diff_ratio(output_a, output_b)

    if line_diff_ratio > 0.30:
        findings.append(
            DifferentialFinding(
                kind="line_diff",
                severity="warning",
                message=(
                    f"Outputs differ by {line_diff_ratio:.1%} of lines "
                    f"(threshold: >30%)"
                ),
                details={
                    "line_diff_ratio": f"{line_diff_ratio:.4f}",
                },
            )
        )

    agreement_ratio = max(0.0, 1.0 - line_diff_ratio)

    return DifferentialReport(
        model_a="",
        model_b="",
        findings=tuple(findings),
        agreement_ratio=agreement_ratio,
    )


def compare_verifier_findings(
    a: tuple[VerificationFinding, ...],
    b: tuple[VerificationFinding, ...],
) -> tuple[DifferentialFinding, ...]:
    def _key(f: VerificationFinding) -> tuple[str, str, str]:
        return (f.verifier_kind, f.file_path, f.message)

    keys_a = frozenset(_key(f) for f in a)
    keys_b = frozenset(_key(f) for f in b)

    only_in_a = keys_a - keys_b
    only_in_b = keys_b - keys_a

    if not only_in_a and not only_in_b:
        return ()

    return (
        DifferentialFinding(
            kind="verifier_disagreement",
            severity="warning",
            message=(
                f"Verifier findings diverge: {len(only_in_a)} only in model_a, "
                f"{len(only_in_b)} only in model_b"
            ),
            details={
                "only_in_a_count": str(len(only_in_a)),
                "only_in_b_count": str(len(only_in_b)),
                "sample_only_in_a": next(iter(str(k) for k in only_in_a), ""),
                "sample_only_in_b": next(iter(str(k) for k in only_in_b), ""),
            },
        ),
    )


__all__ = [
    "DifferentialFinding",
    "DifferentialReport",
    "compare_outputs",
    "compare_verifier_findings",
]
