from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Literal, Mapping

from backend.App.spec.domain.spec_document import SpecDocument

Severity = Literal["error", "warning", "info"]

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Purpose",
    "Public Contract",
    "Behaviour",
)

OPTIONAL_SECTIONS: tuple[str, ...] = (
    "Ubiquitous Language",
    "Invariants",
    "Errors & Failures",
    "Errors and Failures",
    "Examples",
    "Out of Scope",
    "Open Questions",
)

REVIEW_ELIGIBLE_STATUSES = frozenset({"reviewed", "stable"})


@dataclass(frozen=True)
class DocumentFinding:
    code: str
    severity: Severity
    message: str
    spec_id: str
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentValidationResult:
    ok: bool
    findings: tuple[DocumentFinding, ...] = field(default_factory=tuple)


def _missing_required_sections(document: SpecDocument) -> list[str]:
    present = {title.strip().lower() for title, _ in document.sections}
    missing = []
    for required in REQUIRED_SECTIONS:
        if required.strip().lower() not in present:
            missing.append(required)
    return missing


def _has_open_questions_text(document: SpecDocument) -> bool:
    block = document.section("Open Questions").strip()
    if not block:
        return False
    placeholder_lines = {"- none", "none", "—", "-"}
    non_placeholder_lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip() and line.strip().lower() not in placeholder_lines
    ]
    return bool(non_placeholder_lines)


def _validate_single_document(document: SpecDocument) -> list[DocumentFinding]:
    spec_id = document.frontmatter.spec_id
    findings: list[DocumentFinding] = []

    missing = _missing_required_sections(document)
    for section_name in missing:
        findings.append(
            DocumentFinding(
                code="missing_required_section",
                severity="error",
                message=f"Required section '{section_name}' is missing.",
                spec_id=spec_id,
            )
        )

    if document.frontmatter.status in REVIEW_ELIGIBLE_STATUSES:
        if _has_open_questions_text(document):
            findings.append(
                DocumentFinding(
                    code="open_questions_blocks_review",
                    severity="error",
                    message=(
                        f"Status '{document.frontmatter.status}' requires "
                        "Open Questions to be empty or 'none'."
                    ),
                    spec_id=spec_id,
                )
            )

    for target in document.frontmatter.codegen_targets:
        if not target.strip():
            findings.append(
                DocumentFinding(
                    code="empty_codegen_target",
                    severity="error",
                    message="codegen_targets contains an empty entry.",
                    spec_id=spec_id,
                )
            )

    return findings


def _validate_dependency_graph(
    documents: Mapping[str, SpecDocument],
) -> list[DocumentFinding]:
    known_ids = set(documents.keys())
    findings: list[DocumentFinding] = []

    edges: dict[str, list[str]] = {}
    for spec_id, document in documents.items():
        valid_deps: list[str] = []
        for dependency in document.frontmatter.depends_on:
            if dependency not in known_ids:
                findings.append(
                    DocumentFinding(
                        code="missing_dependency",
                        severity="error",
                        message=(
                            f"Spec depends on unknown spec id "
                            f"{dependency!r}."
                        ),
                        spec_id=spec_id,
                        refs=(dependency,),
                    )
                )
                continue
            valid_deps.append(dependency)
        edges[spec_id] = valid_deps

    cycle = _detect_cycle(edges)
    if cycle:
        findings.append(
            DocumentFinding(
                code="dependency_cycle",
                severity="error",
                message=(
                    "Cyclic depends_on detected: "
                    + " -> ".join(cycle + (cycle[0],))
                ),
                spec_id=cycle[0],
                refs=cycle,
            )
        )

    return findings


def _detect_cycle(edges: Mapping[str, list[str]]) -> tuple[str, ...]:
    visited: set[str] = set()
    for start in edges:
        if start in visited:
            continue
        on_stack: set[str] = set()
        parent: dict[str, str] = {}
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(edges.get(start, ())))]
        on_stack.add(start)
        while stack:
            node, iterator = stack[-1]
            next_dep = next(iterator, None)
            if next_dep is None:
                on_stack.discard(node)
                visited.add(node)
                stack.pop()
                continue
            if next_dep in on_stack:
                cycle = [next_dep]
                walker: str | None = node
                while walker is not None and walker != next_dep:
                    cycle.append(walker)
                    walker = parent.get(walker)
                cycle.reverse()
                return tuple(cycle)
            if next_dep in visited:
                continue
            parent[next_dep] = node
            on_stack.add(next_dep)
            stack.append((next_dep, iter(edges.get(next_dep, ()))))
    return ()


def validate_documents(
    documents: Iterable[SpecDocument],
) -> DocumentValidationResult:
    indexed = {
        document.frontmatter.spec_id: document for document in documents
    }
    findings: list[DocumentFinding] = []
    for document in indexed.values():
        findings.extend(_validate_single_document(document))
    findings.extend(_validate_dependency_graph(indexed))
    ok = not any(finding.severity == "error" for finding in findings)
    return DocumentValidationResult(ok=ok, findings=tuple(findings))


def validate_one(document: SpecDocument) -> DocumentValidationResult:
    return validate_documents([document])


__all__ = [
    "DocumentFinding",
    "DocumentValidationResult",
    "REQUIRED_SECTIONS",
    "Severity",
    "validate_documents",
    "validate_one",
]
