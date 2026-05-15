from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_FRONTMATTER_PATTERN = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*\n(?P<rest>.*)\Z",
    re.DOTALL,
)
_AMBIGUITY_MARKERS = ("TODO", "FIXME", "???", "TBD")
_CLARIFICATION_MARKER = "NEEDS_CLARIFICATION"


class DocumentParseError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentNode:
    spec_id: str
    agent: str
    step_id: str
    version: int
    depends_on: tuple[str, ...]
    produces: tuple[str, ...]
    spec_hash: str
    path: str
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "agent": self.agent,
            "step_id": self.step_id,
            "version": self.version,
            "depends_on": list(self.depends_on),
            "produces": list(self.produces),
            "spec_hash": self.spec_hash,
            "path": self.path,
        }


@dataclass(frozen=True)
class DocumentEdge:
    from_spec: str
    to_spec: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_spec, "to": self.to_spec, "kind": self.kind}


@dataclass(frozen=True)
class DocumentGraph:
    nodes: tuple[DocumentNode, ...]
    edges: tuple[DocumentEdge, ...]
    produces_index: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "produces": {key: list(values) for key, values in self.produces_index.items()},
        }


def _str_list(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise DocumentParseError(f"{field_name} must be a list, got {type(raw).__name__}")
    items: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise DocumentParseError(f"{field_name} entries must be non-empty strings")
        items.append(entry.strip())
    return tuple(items)


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DocumentParseError(f"frontmatter {key!r} is required and must be a non-empty string")
    return value.strip()


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentParseError(f"frontmatter {key!r} must be an integer")
    if value < 1:
        raise DocumentParseError(f"frontmatter {key!r} must be >= 1, got {value}")
    return value


def parse_document(text: str, *, path: str) -> DocumentNode:
    match = _FRONTMATTER_PATTERN.match(text)
    if match is None:
        raise DocumentParseError(f"{path}: missing YAML frontmatter delimited by '---'")
    body_yaml = match.group("body")
    body_md = match.group("rest")
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(body_yaml)
    except ImportError as exc:
        raise DocumentParseError(
            f"{path}: pyyaml is required to parse document frontmatter; install pyyaml"
        ) from exc
    except Exception as exc:
        raise DocumentParseError(f"{path}: invalid YAML frontmatter — {exc}") from exc
    if not isinstance(loaded, dict):
        raise DocumentParseError(f"{path}: frontmatter must be a YAML object")
    body_hash = hashlib.sha256(body_md.encode("utf-8")).hexdigest()
    declared_hash = loaded.get("spec_hash")
    spec_hash = (
        declared_hash.strip()
        if isinstance(declared_hash, str) and declared_hash.strip()
        else f"sha256:{body_hash}"
    )
    return DocumentNode(
        spec_id=_required_str(loaded, "spec_id"),
        agent=_required_str(loaded, "agent"),
        step_id=_required_str(loaded, "step_id"),
        version=_required_int(loaded, "version"),
        depends_on=_str_list(loaded.get("depends_on"), field_name="depends_on"),
        produces=_str_list(loaded.get("produces"), field_name="produces"),
        spec_hash=spec_hash,
        path=path,
        body=body_md,
    )


def build_graph(nodes: list[DocumentNode]) -> DocumentGraph:
    edges: list[DocumentEdge] = []
    produces_index: dict[str, list[str]] = {}
    for node in nodes:
        for dependency in node.depends_on:
            edges.append(
                DocumentEdge(from_spec=node.spec_id, to_spec=dependency, kind="depends_on")
            )
        for produced in node.produces:
            produces_index.setdefault(produced, []).append(node.spec_id)
    return DocumentGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        produces_index={k: tuple(v) for k, v in produces_index.items()},
    )


@dataclass(frozen=True)
class ValidationFinding:
    check: str
    severity: str
    spec_id: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "spec_id": self.spec_id,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[ValidationFinding, ...]

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def verdict(self) -> str:
        return "fail" if self.has_errors else "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [finding.to_dict() for finding in self.findings],
            "errors": sum(1 for f in self.findings if f.severity == "error"),
            "warnings": sum(1 for f in self.findings if f.severity == "warning"),
        }


def _check_unresolved_clarifications(node: DocumentNode) -> Optional[ValidationFinding]:
    if _CLARIFICATION_MARKER not in node.body:
        return None
    return ValidationFinding(
        check="unresolved_clarifications",
        severity="error",
        spec_id=node.spec_id,
        detail=f"{node.path}: document contains {_CLARIFICATION_MARKER} marker — answer questions before promoting",
    )


def _check_dangling_references(graph: DocumentGraph) -> list[ValidationFinding]:
    known_specs = {node.spec_id for node in graph.nodes}
    findings: list[ValidationFinding] = []
    for node in graph.nodes:
        for dependency in node.depends_on:
            if dependency not in known_specs:
                findings.append(
                    ValidationFinding(
                        check="dangling_references",
                        severity="error",
                        spec_id=node.spec_id,
                        detail=f"depends on unknown spec_id {dependency!r}",
                    )
                )
    return findings


def _check_duplicate_definitions(graph: DocumentGraph) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for produced_key, spec_ids in graph.produces_index.items():
        if len(spec_ids) > 1:
            findings.append(
                ValidationFinding(
                    check="duplicate_definitions",
                    severity="error",
                    spec_id=spec_ids[0],
                    detail=(
                        f"produces key {produced_key!r} defined in multiple specs: "
                        + ", ".join(spec_ids)
                    ),
                )
            )
    return findings


def _check_ambiguity(node: DocumentNode) -> Optional[ValidationFinding]:
    upper_body = node.body.upper()
    for marker in _AMBIGUITY_MARKERS:
        if marker in upper_body:
            return ValidationFinding(
                check="ambiguity",
                severity="warning",
                spec_id=node.spec_id,
                detail=f"{node.path}: contains ambiguity marker {marker!r}",
            )
    return None


def validate_graph(graph: DocumentGraph) -> ValidationReport:
    findings: list[ValidationFinding] = []
    for node in graph.nodes:
        clarification = _check_unresolved_clarifications(node)
        if clarification is not None:
            findings.append(clarification)
        ambiguity = _check_ambiguity(node)
        if ambiguity is not None:
            findings.append(ambiguity)
    findings.extend(_check_dangling_references(graph))
    findings.extend(_check_duplicate_definitions(graph))
    return ValidationReport(findings=tuple(findings))


__all__ = [
    "DocumentEdge",
    "DocumentGraph",
    "DocumentNode",
    "DocumentParseError",
    "ValidationFinding",
    "ValidationReport",
    "build_graph",
    "parse_document",
    "validate_graph",
]
